"""
check_aqi.py
------------
Script chạy định kỳ (qua GitHub Actions cron, mỗi 30 phút) để:
  1. Lấy dữ liệu AQI hiện tại từ AQICN API cho thành phố cấu hình sẵn.
  2. Phân loại AQI theo ngưỡng EPA.
  3. Lưu kết quả vào SQLite (bảng aqi_history).
  4. Gửi thông báo Discord trong 2 trường hợp (ưu tiên theo thứ tự):
     a. Ngưỡng thay đổi so với lần chạy trước (tăng hoặc giảm) -> luôn báo,
        vì đây là thông tin quan trọng bất kể giờ giấc.
     b. Không đổi ngưỡng, nhưng đang là 1 trong các mốc giờ báo cáo định kỳ
        (6h/12h/18h giờ Việt Nam) -> vẫn báo hiện trạng, để có cập nhật đều
        đặn trong ngày dù AQI không biến động.
     Ngoài 2 trường hợp trên (không đổi ngưỡng và không phải giờ báo cáo) ->
     không gửi gì cả, tránh spam kênh Discord.
  5. Riêng biệt, KHÔNG ảnh hưởng tới logic ở bước 4: vào 21h hằng ngày (giờ
     Việt Nam), gửi thêm 1 tin dự báo AQI cho ngày mai (ước tính từ dữ liệu
     dự báo PM2.5/PM10 của AQICN), giúp người dùng chuẩn bị trước.
  6. Mỗi tin cảnh báo/báo cáo (bước 4) đều kèm thêm 1 dòng so sánh AQI với
     cùng giờ hôm qua (nếu có đủ dữ liệu), giúp người đọc thấy ngay xu hướng
     tốt lên hay xấu đi mà không cần tự tra cứu.

Biến môi trường bắt buộc:
  - AQICN_TOKEN     : API token lấy tại https://aqicn.org/data-platform/token/
  - DISCORD_WEBHOOK : URL webhook của kênh Discord muốn nhận cảnh báo

Biến môi trường tuỳ chọn:
  - AQI_CITY : tên thành phố/trạm AQICN, mặc định "hanoi"
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from aqi_common import (
    CITY,
    LEVEL_COLORS,
    classify_aqi,
    forecast_pm_to_aqi,
    get_db_connection,
    get_last_record,
    get_record_near,
    insert_record,
)

AQICN_API_URL = "https://api.waqi.info/feed/{city}/?token={token}"

# Múi giờ Việt Nam, dùng để xác định mốc báo cáo định kỳ 6h/12h/18h theo giờ
# địa phương, bất kể GitHub Actions runner chạy ở giờ UTC nào.
VN_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
SCHEDULED_REPORT_HOURS = {6, 12, 18}

# Mốc giờ gửi dự báo AQI ngày mai — tách riêng khỏi SCHEDULED_REPORT_HOURS vì
# đây là 1 loại tin nhắn khác (dự báo tương lai, không phải hiện trạng), chạy
# độc lập, không ảnh hưởng tới logic ưu tiên đổi ngưỡng / báo cáo định kỳ.
FORECAST_HOUR = 21


def is_scheduled_report_window(now: datetime | None = None) -> bool:
    """True nếu thời điểm hiện tại (giờ VN) nằm trong 30 phút đầu của 1 trong
    các mốc báo cáo định kỳ (6h/12h/18h). Cửa sổ 30 phút để khớp với chu kỳ
    chạy mỗi 30 phút của cron, phòng trường hợp GitHub Actions bị trễ lịch.
    """
    now_vn = (now or datetime.now(VN_TIMEZONE)).astimezone(VN_TIMEZONE)
    return now_vn.hour in SCHEDULED_REPORT_HOURS and now_vn.minute < 30


def is_forecast_window(now: datetime | None = None) -> bool:
    """True nếu thời điểm hiện tại (giờ VN) nằm trong 30 phút đầu của mốc 21h
    — thời điểm gửi dự báo AQI ngày mai. Độc lập với is_scheduled_report_window."""
    now_vn = (now or datetime.now(VN_TIMEZONE)).astimezone(VN_TIMEZONE)
    return now_vn.hour == FORECAST_HOUR and now_vn.minute < 30


def fetch_current_aqi(city: str, token: str) -> int:
    """Gọi AQICN API và trả về giá trị AQI hiện tại (số nguyên).

    Raise RuntimeError nếu API trả lỗi hoặc dữ liệu không hợp lệ.
    """
    url = AQICN_API_URL.format(city=city, token=token)
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "ok":
        raise RuntimeError(f"AQICN API trả về lỗi: {data}")

    aqi_value = data["data"]["aqi"]
    if not isinstance(aqi_value, int):
        # AQICN đôi khi trả "-" khi trạm tạm thời không có dữ liệu
        raise RuntimeError(f"Giá trị AQI không hợp lệ (trạm có thể đang offline): {aqi_value}")

    return aqi_value


def fetch_tomorrow_forecast_aqi(city: str, token: str):
    """Gọi AQICN API, lấy dữ liệu dự báo PM2.5/PM10 cho NGÀY MAI (giờ VN) và
    quy đổi sang AQI. Trả về None nếu trạm không có dữ liệu dự báo cho ngày mai.

    Đây là API call RIÊNG, tách biệt hoàn toàn với fetch_current_aqi() ở trên
    — không sửa hay dùng chung logic với hàm đó, đảm bảo không ảnh hưởng tới
    luồng xử lý AQI hiện tại đã có.
    """
    url = AQICN_API_URL.format(city=city, token=token)
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "ok":
        raise RuntimeError(f"AQICN API trả về lỗi: {data}")

    forecast_daily = data.get("data", {}).get("forecast", {}).get("daily", {})
    tomorrow = (datetime.now(VN_TIMEZONE) + timedelta(days=1)).strftime("%Y-%m-%d")

    pm25_max = next((d["max"] for d in forecast_daily.get("pm25", []) if d.get("day") == tomorrow), None)
    pm10_max = next((d["max"] for d in forecast_daily.get("pm10", []) if d.get("day") == tomorrow), None)

    return forecast_pm_to_aqi(pm25_max, pm10_max)


def send_discord_forecast(webhook_url: str, forecast_aqi: int, forecast_level: str, city: str) -> None:
    """Gửi tin dự báo AQI ngày mai vào lúc 21h — độc lập với send_discord_alert
    và send_discord_status_report, không tái sử dụng hay sửa 2 hàm đó."""
    embed = {
        "title": f"🔮 Dự báo AQI ngày mai — {city.title()}",
        "description": (
            f"**AQI dự kiến:** {forecast_aqi}\n"
            f"**Mức dự kiến:** {forecast_level}\n"
            "(Ước tính từ dữ liệu dự báo PM2.5/PM10 của AQICN)"
        ),
        "color": int(LEVEL_COLORS.get(forecast_level, "#808080").lstrip("#"), 16),
    }
    payload = {
        "content": (
            f"@everyone 🔮 Dự báo AQI {city.title()} ngày mai: {forecast_level} ({forecast_aqi})"
        ),
        "embeds": [embed],
        "allowed_mentions": {"parse": ["everyone"]},
    }

    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def format_yesterday_comparison(current_aqi: int, yesterday_record) -> str:
    """Tạo câu mô tả đầy đủ so sánh AQI hiện tại với bản ghi gần "cùng giờ hôm
    qua" nhất (yesterday_record, lấy từ get_record_near) — dùng làm value của
    1 field riêng trong embed, cho dễ quan sát hơn là lẫn vào đoạn mô tả.
    Trả về chuỗi rỗng nếu không có dữ liệu đủ gần để so sánh."""
    if yesterday_record is None:
        return ""
    _, yesterday_aqi, _ = yesterday_record
    delta = current_aqi - yesterday_aqi
    if delta > 0:
        return f"🔺 Tăng {delta} điểm ({yesterday_aqi} → {current_aqi})"
    if delta < 0:
        return f"🔻 Giảm {abs(delta)} điểm ({yesterday_aqi} → {current_aqi})"
    return f"➖ Không đổi (vẫn {current_aqi})"


def format_yesterday_delta_short(current_aqi: int, yesterday_record) -> str:
    """Dạng RÚT GỌN của so sánh hôm qua, dùng trong "content" — phần duy nhất
    hiện ra trên push notification/lock screen, nên phải ngắn. Trả về chuỗi
    rỗng nếu không có dữ liệu để so sánh."""
    if yesterday_record is None:
        return ""
    _, yesterday_aqi, _ = yesterday_record
    delta = current_aqi - yesterday_aqi
    if delta > 0:
        return f" (▲{delta} so hôm qua)"
    if delta < 0:
        return f" (▼{abs(delta)} so hôm qua)"
    return " (= hôm qua)"


def send_discord_alert(
    webhook_url: str,
    old_aqi: int,
    old_level: str,
    new_aqi: int,
    new_level: str,
    timestamp: str,
    city: str,
    comparison_text: str = "",
    comparison_short: str = "",
) -> None:
    """Gửi thông báo đổi ngưỡng AQI vào kênh Discord thông qua Webhook."""
    embed = {
        "title": f"⚠️ Cảnh báo thay đổi mức AQI — {city.title()}",
        "description": (
            f"**AQI:** {old_aqi} → {new_aqi}\n"
            f"**Ngưỡng:** {old_level} → {new_level}\n"
            f"**Thời gian:** {timestamp} (UTC)"
        ),
        "color": int(LEVEL_COLORS.get(new_level, "#808080").lstrip("#"), 16),
    }
    if comparison_text:
        # Dùng "fields" thay vì nhét vào description -> Discord hiển thị
        # thành 1 khối riêng có tiêu đề in đậm, dễ quan sát hơn hẳn so với
        # 1 dòng text lẫn trong đoạn mô tả.
        embed["fields"] = [{"name": "📅 So với cùng giờ hôm qua", "value": comparison_text, "inline": False}]

    payload = {
        # @everyone -> mọi thành viên server đều bị ping (kể cả offline), nên
        # ai vào server là tự động nhận thông báo, không cần khai báo User ID.
        # Push notification trên điện thoại chỉ hiện được nội dung của "content",
        # KHÔNG hiện nội dung bên trong "embeds" -> phải nhét luôn thông tin tóm
        # tắt (AQI cũ->mới, ngưỡng cũ->mới, và cả so sánh hôm qua) vào đây.
        "content": (
            f"@everyone ⚠️ AQI {city.title()} đổi mức: "
            f"{old_level} ({old_aqi}) → {new_level} ({new_aqi}){comparison_short}"
        ),
        "embeds": [embed],
        # Discord mặc định KHÔNG ping thật dù content có chữ "@everyone", trừ
        # khi "parse" khai báo rõ -> bắt buộc phải có dòng này thì ping mới hoạt động.
        "allowed_mentions": {"parse": ["everyone"]},
    }

    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def send_discord_status_report(
    webhook_url: str,
    aqi_value: int,
    level: str,
    timestamp: str,
    city: str,
    comparison_text: str = "",
    comparison_short: str = "",
) -> None:
    """Gửi báo cáo AQI hiện trạng vào mốc giờ cố định (6h/12h/18h), dùng khi
    ngưỡng KHÔNG đổi nhưng vẫn muốn cập nhật định kỳ cho người theo dõi."""
    embed = {
        "title": f"📊 Báo cáo AQI định kỳ — {city.title()}",
        "description": f"**AQI hiện tại:** {aqi_value}\n**Mức:** {level}\n**Thời gian:** {timestamp} (UTC)",
        "color": int(LEVEL_COLORS.get(level, "#808080").lstrip("#"), 16),
    }
    if comparison_text:
        embed["fields"] = [{"name": "📅 So với cùng giờ hôm qua", "value": comparison_text, "inline": False}]

    payload = {
        "content": f"@everyone 📊 Báo cáo AQI {city.title()}: hiện đang ở mức {level} ({aqi_value}){comparison_short}",
        "embeds": [embed],
        "allowed_mentions": {"parse": ["everyone"]},
    }

    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def main() -> int:
    aqicn_token = os.environ.get("AQICN_TOKEN")
    discord_webhook = os.environ.get("DISCORD_WEBHOOK")

    if not aqicn_token:
        print("Lỗi: thiếu biến môi trường AQICN_TOKEN", file=sys.stderr)
        return 1
    if not discord_webhook:
        print("Lỗi: thiếu biến môi trường DISCORD_WEBHOOK", file=sys.stderr)
        return 1

    try:
        aqi_value = fetch_current_aqi(CITY, aqicn_token)
    except Exception as exc:
        print(f"Lỗi khi lấy dữ liệu AQICN: {exc}", file=sys.stderr)
        return 1

    new_level = classify_aqi(aqi_value)

    conn = get_db_connection()
    last_record = get_last_record(conn)  # None nếu đây là lần chạy đầu tiên
    timestamp = insert_record(conn, aqi_value, new_level)
    conn.close()

    print(f"[{timestamp}] {CITY}: AQI={aqi_value} -> level={new_level}")

    # So sánh với cùng giờ hôm qua (không ảnh hưởng logic ưu tiên bên dưới,
    # chỉ tạo thêm 1 dòng mô tả để nhét vào nội dung tin nhắn nếu có gửi).
    yesterday_target = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    conn = get_db_connection()
    yesterday_record = get_record_near(conn, yesterday_target)
    conn.close()
    comparison_text = format_yesterday_comparison(aqi_value, yesterday_record)
    comparison_short = format_yesterday_delta_short(aqi_value, yesterday_record)

    level_changed = last_record is not None and last_record[2] != new_level

    if level_changed:
        _, old_aqi, old_level = last_record
        print(f"Ngưỡng thay đổi: {old_level} -> {new_level}. Đang gửi cảnh báo Discord...")
        send_discord_alert(discord_webhook, old_aqi, old_level, aqi_value, new_level, timestamp, CITY, comparison_text, comparison_short)
        print("Đã gửi cảnh báo Discord thành công.")
    elif is_scheduled_report_window():
        print("Đang trong khung giờ báo cáo định kỳ (6h/12h/18h). Đang gửi báo cáo Discord...")
        send_discord_status_report(discord_webhook, aqi_value, new_level, timestamp, CITY, comparison_text, comparison_short)
        print("Đã gửi báo cáo Discord thành công.")
    elif last_record is None:
        print("Chưa có dữ liệu trước đó và không phải giờ báo cáo, bỏ qua (lần chạy đầu tiên).")
    else:
        print(f"Ngưỡng không đổi ({new_level}) và không phải giờ báo cáo, không gửi thông báo.")

    # --- Dự báo AQI ngày mai (21h) -----------------------------------------
    # Khối này HOÀN TOÀN ĐỘC LẬP với logic phía trên (đổi ngưỡng / báo cáo
    # định kỳ) — không đọc, không ghi đè bất kỳ biến nào ở trên, chỉ thêm 1
    # tin nhắn riêng khi đúng khung giờ 21h, không làm thay đổi hành vi gốc.
    if is_forecast_window():
        try:
            forecast_aqi = fetch_tomorrow_forecast_aqi(CITY, aqicn_token)
        except Exception as exc:
            print(f"Lỗi khi lấy dự báo AQICN: {exc}", file=sys.stderr)
            forecast_aqi = None

        if forecast_aqi is not None:
            forecast_level = classify_aqi(forecast_aqi)
            print(f"Dự báo ngày mai: AQI={forecast_aqi} -> level={forecast_level}. Đang gửi Discord...")
            send_discord_forecast(discord_webhook, forecast_aqi, forecast_level, CITY)
            print("Đã gửi dự báo Discord thành công.")
        else:
            print("Trạm không có dữ liệu dự báo cho ngày mai, bỏ qua.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

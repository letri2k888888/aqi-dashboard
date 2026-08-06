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
        đặn trong ngày dù AQI không biến động. Khung giờ này kéo dài 30 phút
        (phút 0-29) nên nếu lần chạy đầu (đúng giờ) bị bỏ lỡ vì trạm đứng
        hình, lần chạy phụ 5 phút sau (xem cron-job.org) vẫn còn trong khung
        và có thể gửi thay — mỗi khung giờ chỉ gửi đúng 1 lần (chống trùng
        qua system_status.last_report_marker).
     Ngoài 2 trường hợp trên (không đổi ngưỡng và không phải giờ báo cáo) ->
     không gửi gì cả, tránh spam kênh Discord.
  5. [MẶC ĐỊNH TẮT — xem ghi chú bên dưới] Riêng biệt, không ảnh hưởng tới
     logic ở bước 4: vào 21h hằng ngày (giờ Việt Nam), gửi thêm 1 tin dự báo
     AQI cho ngày mai (ước tính từ giá trị trung bình dự báo PM2.5/PM10 của
     AQICN).
  6. Mỗi tin cảnh báo/báo cáo (bước 4) đều kèm thêm 1 dòng so sánh AQI với
     cùng giờ hôm qua (nếu có đủ dữ liệu), giúp người đọc thấy ngay xu hướng
     tốt lên hay xấu đi mà không cần tự tra cứu.
  7. Nếu gọi AQICN API thất bại 3 lần chạy LIÊN TIẾP trở lên, gửi 1 tin cảnh
     báo sự cố hệ thống (định dạng nổi bật, @everyone, giống bước 4) — chỉ
     gửi 1 lần cho mỗi đợt lỗi (không lặp lại mỗi 30 phút), tự reset khi lấy
     dữ liệu thành công trở lại. Vá lỗ hổng "im lặng khi hỏng": trước đây lỗi
     chỉ ghi log, không ai biết trừ khi tự vào xem GitHub Actions.

GHI CHÚ QUAN TRỌNG về tính năng dự báo (bước 5): đã kiểm chứng thực tế và
phát hiện `forecast.daily` của AQICN KHÔNG khớp với số đo thật của trạm đang
theo dõi — ví dụ ngày 2026-08-03, dự báo cho ĐÚNG NGÀY ĐÓ quy đổi ra AQI≈147
(trung bình) trong khi số đo thật tại trạm chỉ 93 (chênh hơn 1.5 lần ngay cả
khi so cùng ngày). Nhiều khả năng dữ liệu forecast lấy từ mô hình khí tượng
khu vực rộng, không gắn với đúng cảm biến của trạm — nên KHÔNG đáng tin cậy
để cảnh báo người dùng. Vì vậy tính năng này mặc định TẮT (xem FORECAST_ENABLED
bên dưới); code vẫn giữ nguyên trong repo để tham khảo/bật lại nếu sau này có
nguồn dự báo đáng tin cậy hơn.

Biến môi trường bắt buộc:
  - AQICN_TOKEN     : API token lấy tại https://aqicn.org/data-platform/token/
  - DISCORD_WEBHOOK : URL webhook của kênh Discord muốn nhận cảnh báo

Biến môi trường tuỳ chọn:
  - AQI_CITY          : tên thành phố/trạm AQICN, mặc định "hanoi"
  - FORECAST_ENABLED  : "true" để bật lại tin dự báo 21h, mặc định "false"
                        (tắt) do dữ liệu forecast không đáng tin cậy — xem
                        ghi chú ở trên.
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
    format_vn_time,
    get_db_connection,
    get_last_record,
    get_last_report_marker,
    get_record_near,
    has_sent_failure_alert,
    insert_record,
    mark_failure_alert_sent,
    record_fetch_failure,
    record_fetch_success,
    set_last_report_marker,
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

# Mặc định TẮT: đã kiểm chứng dữ liệu forecast.daily của AQICN không khớp với
# số đo thật của trạm đang theo dõi (chênh hơn 1.5 lần ngay cả khi so cùng
# ngày) — xem ghi chú chi tiết ở đầu file. Dùng "or" (không phải giá trị mặc
# định của .get()) vì lý do tương tự AQI_CITY: GitHub Actions luôn set biến
# env dù rỗng khi chưa cấu hình vars.
FORECAST_ENABLED = (os.environ.get("FORECAST_ENABLED") or "false").strip().lower() == "true"

# Số lần lấy dữ liệu AQICN thất bại LIÊN TIẾP trước khi gửi cảnh báo sự cố hệ
# thống — chờ vài lần thay vì báo ngay lần đầu, để tránh báo động vì 1 lỗi
# mạng thoáng qua (transient), chỉ báo khi thực sự là sự cố kéo dài.
FAILURE_ALERT_THRESHOLD = 3

# Số giờ tối đa cho phép giữa lần đo gần nhất của TRẠM và thời điểm hiện tại,
# trước khi coi là trạm "đứng hình" — API vẫn trả status=ok (không phải lỗi
# HTTP) nhưng số liệu không còn được cập nhật thật. AQICN thường cập nhật
# khoảng mỗi giờ, nên 3 giờ là ngưỡng an toàn, tránh báo nhầm khi trạm chỉ
# đang chậm cập nhật bình thường.
STALE_STATION_HOURS = 3


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


def fetch_current_aqi(city: str, token: str):
    """Gọi AQICN API, trả về (aqi_value, station_time_iso).

    station_time_iso là thời điểm TRẠM tự ghi nhận lần đo gần nhất (không
    phải thời điểm mình gọi API) — dùng để phát hiện trạm "đứng hình" (API
    vẫn trả status=ok nhưng số liệu đã cũ, không phải lỗi HTTP thông thường).

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

    station_time_iso = data.get("data", {}).get("time", {}).get("iso")

    return aqi_value, station_time_iso


def is_station_data_stale(station_time_iso, now: datetime | None = None) -> bool:
    """True nếu thời điểm trạm tự ghi nhận (station_time_iso) đã cách hiện
    tại quá STALE_STATION_HOURS giờ, hoặc không đọc được thời gian trạm —
    tức API vẫn trả status=ok nhưng số liệu thực chất đã cũ/đứng hình."""
    if not station_time_iso:
        return True  # không có thời gian trạm -> không thể xác nhận độ mới, coi là đáng ngờ
    try:
        station_time = datetime.fromisoformat(station_time_iso)
    except ValueError:
        return True
    now = now or datetime.now(timezone.utc)
    age_hours = (now - station_time).total_seconds() / 3600
    return age_hours > STALE_STATION_HOURS


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

    # Dùng "avg" (trung bình cả ngày) thay vì "max" (đỉnh điểm trong ngày) —
    # phản ánh mức ô nhiễm điển hình của ngày mai thay vì kịch bản xấu nhất,
    # tránh gây cảnh báo quá mức. Tin nhắn gửi đi phải nói rõ đây là giá trị
    # trung bình, không phải mức cao nhất có thể xảy ra trong ngày.
    pm25_avg = next((d["avg"] for d in forecast_daily.get("pm25", []) if d.get("day") == tomorrow), None)
    pm10_avg = next((d["avg"] for d in forecast_daily.get("pm10", []) if d.get("day") == tomorrow), None)

    return forecast_pm_to_aqi(pm25_avg, pm10_avg)


def send_discord_forecast(webhook_url: str, forecast_aqi: int, forecast_level: str, city: str) -> None:
    """Gửi tin dự báo AQI ngày mai vào lúc 21h — độc lập với send_discord_alert
    và send_discord_status_report, không tái sử dụng hay sửa 2 hàm đó."""
    embed = {
        "title": f"🔮 Dự báo AQI ngày mai — {city.title()}",
        "description": (
            f"**AQI dự kiến (trung bình cả ngày):** {forecast_aqi}\n"
            f"**Mức dự kiến:** {forecast_level}\n"
            "⚠️ Đây là **giá trị trung bình** ước tính từ dự báo PM2.5/PM10 của AQICN — "
            "mức cao nhất thực tế trong ngày có thể vượt con số này."
        ),
        "color": int(LEVEL_COLORS.get(forecast_level, "#808080").lstrip("#"), 16),
    }
    payload = {
        "content": (
            f"@everyone 🔮 Dự báo AQI {city.title()} ngày mai (TB): {forecast_level} ({forecast_aqi})"
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


def send_discord_error_alert(webhook_url: str, error_message: str, consecutive_failures: int, city: str) -> None:
    """Gửi cảnh báo khi hệ thống lấy dữ liệu AQICN thất bại LIÊN TIẾP nhiều
    lần. Cố tình dùng cùng phong cách nổi bật với send_discord_alert
    (@everyone, embed riêng) để người dùng chú ý ngay — đây là tin quan
    trọng: có thể đang không nhận được cảnh báo AQI thật nào trong lúc lỗi."""
    embed = {
        "title": f"🚨 Hệ thống theo dõi AQI gặp sự cố — {city.title()}",
        "description": (
            f"Không lấy được dữ liệu AQI trong **{consecutive_failures} lần chạy liên tiếp**.\n"
            f"**Lỗi gần nhất:** {error_message}\n"
            "Dữ liệu AQI hiện đang **không được cập nhật** — số liệu cũ có thể không còn đúng."
        ),
        "color": 0x2C2F33,  # den xam, phan biet ro voi mau theo muc AQI cua cac tin khac
    }
    payload = {
        "content": (
            f"@everyone 🚨 Hệ thống theo dõi AQI {city.title()} đang gặp sự cố "
            f"({consecutive_failures} lần lỗi liên tiếp) — dữ liệu có thể đang cũ."
        ),
        "embeds": [embed],
        "allowed_mentions": {"parse": ["everyone"]},
    }

    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


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
            f"**Thời gian:** {format_vn_time(timestamp)} (giờ VN)"
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
        "description": f"**AQI hiện tại:** {aqi_value}\n**Mức:** {level}\n**Thời gian:** {format_vn_time(timestamp)} (giờ VN)",
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


def _handle_fetch_problem(discord_webhook: str, error_message: str) -> None:
    """Dùng chung cho 2 trường hợp coi như "không lấy được dữ liệu tin cậy":
    API lỗi hẳn, hoặc trạm đứng hình (API trả ok nhưng số liệu cũ). Tăng bộ
    đếm lỗi liên tiếp trong system_status, gửi cảnh báo Discord nếu đạt
    ngưỡng FAILURE_ALERT_THRESHOLD và chưa gửi cho đợt lỗi hiện tại."""
    try:
        conn = get_db_connection()
        failures = record_fetch_failure(conn)
        if failures >= FAILURE_ALERT_THRESHOLD and not has_sent_failure_alert(conn):
            print(f"Đã lỗi {failures} lần liên tiếp. Đang gửi cảnh báo sự cố Discord...")
            send_discord_error_alert(discord_webhook, error_message, failures, CITY)
            mark_failure_alert_sent(conn)
            print("Đã gửi cảnh báo sự cố Discord thành công.")
        conn.close()
    except Exception as alert_exc:
        print(f"Lỗi khi xử lý cảnh báo sự cố: {alert_exc}", file=sys.stderr)


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
        aqi_value, station_time_iso = fetch_current_aqi(CITY, aqicn_token)
    except Exception as exc:
        print(f"Lỗi khi lấy dữ liệu AQICN: {exc}", file=sys.stderr)
        _handle_fetch_problem(discord_webhook, str(exc))
        return 1

    if is_station_data_stale(station_time_iso):
        # API trả status=ok (không phải lỗi HTTP) nhưng số liệu trạm đã cũ —
        # xử lý giống hệt 1 lần thất bại, KHÔNG lưu vào lịch sử và KHÔNG chạy
        # tiếp logic thông báo, để tránh nhét số liệu cũ vào SQLite làm sai
        # lệch tính năng "so với hôm qua" hoặc gửi cảnh báo dựa trên số cũ.
        stale_msg = f"Trạm không cập nhật dữ liệu mới (lần đo gần nhất: {station_time_iso})"
        print(f"Cảnh báo: {stale_msg}", file=sys.stderr)
        _handle_fetch_problem(discord_webhook, stale_msg)
        return 1

    new_level = classify_aqi(aqi_value)

    conn = get_db_connection()
    record_fetch_success(conn)  # lấy dữ liệu thành công -> reset bộ đếm lỗi
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
        # Chống gửi trùng khi có nhiều lần thử lại trong cùng 1 khung giờ (ví
        # dụ lần chạy đúng giờ bị "trạm đứng hình" nên bỏ lỡ, lần chạy phụ 5
        # phút sau mới gửi được) — mỗi khung giờ (ngày + giờ) chỉ gửi 1 lần.
        now_vn = datetime.now(VN_TIMEZONE)
        report_marker = f"{now_vn.strftime('%Y-%m-%d')}-{now_vn.hour}"
        conn = get_db_connection()
        already_sent = get_last_report_marker(conn) == report_marker
        if already_sent:
            conn.close()
            print(f"Đã gửi báo cáo cho khung giờ {report_marker} rồi, bỏ qua (tránh trùng lặp).")
        else:
            print("Đang trong khung giờ báo cáo định kỳ (6h/12h/18h). Đang gửi báo cáo Discord...")
            send_discord_status_report(discord_webhook, aqi_value, new_level, timestamp, CITY, comparison_text, comparison_short)
            set_last_report_marker(conn, report_marker)
            conn.close()
            print("Đã gửi báo cáo Discord thành công.")
    elif last_record is None:
        print("Chưa có dữ liệu trước đó và không phải giờ báo cáo, bỏ qua (lần chạy đầu tiên).")
    else:
        print(f"Ngưỡng không đổi ({new_level}) và không phải giờ báo cáo, không gửi thông báo.")

    # --- Dự báo AQI ngày mai (21h) -----------------------------------------
    # Khối này HOÀN TOÀN ĐỘC LẬP với logic phía trên (đổi ngưỡng / báo cáo
    # định kỳ) — không đọc, không ghi đè bất kỳ biến nào ở trên, chỉ thêm 1
    # tin nhắn riêng khi đúng khung giờ 21h, không làm thay đổi hành vi gốc.
    # MẶC ĐỊNH TẮT (FORECAST_ENABLED=false) vì dữ liệu forecast của AQICN
    # không khớp với số đo thật của trạm — xem ghi chú đầu file.
    if FORECAST_ENABLED and is_forecast_window():
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

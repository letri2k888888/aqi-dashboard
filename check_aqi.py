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

Biến môi trường bắt buộc:
  - AQICN_TOKEN     : API token lấy tại https://aqicn.org/data-platform/token/
  - DISCORD_WEBHOOK : URL webhook của kênh Discord muốn nhận cảnh báo

Biến môi trường tuỳ chọn:
  - AQI_CITY : tên thành phố/trạm AQICN, mặc định "hanoi"
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from aqi_common import CITY, LEVEL_COLORS, classify_aqi, get_db_connection, get_last_record, insert_record

AQICN_API_URL = "https://api.waqi.info/feed/{city}/?token={token}"

# Múi giờ Việt Nam, dùng để xác định mốc báo cáo định kỳ 6h/12h/18h theo giờ
# địa phương, bất kể GitHub Actions runner chạy ở giờ UTC nào.
VN_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
SCHEDULED_REPORT_HOURS = {6, 12, 18}


def is_scheduled_report_window(now: datetime | None = None) -> bool:
    """True nếu thời điểm hiện tại (giờ VN) nằm trong 30 phút đầu của 1 trong
    các mốc báo cáo định kỳ (6h/12h/18h). Cửa sổ 30 phút để khớp với chu kỳ
    chạy mỗi 30 phút của cron, phòng trường hợp GitHub Actions bị trễ lịch.
    """
    now_vn = (now or datetime.now(VN_TIMEZONE)).astimezone(VN_TIMEZONE)
    return now_vn.hour in SCHEDULED_REPORT_HOURS and now_vn.minute < 30


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


def send_discord_alert(webhook_url: str, old_aqi: int, old_level: str, new_aqi: int, new_level: str, timestamp: str, city: str) -> None:
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
    payload = {
        # @everyone -> mọi thành viên server đều bị ping (kể cả offline), nên
        # ai vào server là tự động nhận thông báo, không cần khai báo User ID.
        # Push notification trên điện thoại chỉ hiện được nội dung của "content",
        # KHÔNG hiện nội dung bên trong "embeds" -> phải nhét luôn thông tin tóm
        # tắt (AQI cũ->mới, ngưỡng cũ->mới) vào đây.
        "content": (
            f"@everyone ⚠️ AQI {city.title()} đổi mức: "
            f"{old_level} ({old_aqi}) → {new_level} ({new_aqi})"
        ),
        "embeds": [embed],
        # Discord mặc định KHÔNG ping thật dù content có chữ "@everyone", trừ
        # khi "parse" khai báo rõ -> bắt buộc phải có dòng này thì ping mới hoạt động.
        "allowed_mentions": {"parse": ["everyone"]},
    }

    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def send_discord_status_report(webhook_url: str, aqi_value: int, level: str, timestamp: str, city: str) -> None:
    """Gửi báo cáo AQI hiện trạng vào mốc giờ cố định (6h/12h/18h), dùng khi
    ngưỡng KHÔNG đổi nhưng vẫn muốn cập nhật định kỳ cho người theo dõi."""
    embed = {
        "title": f"📊 Báo cáo AQI định kỳ — {city.title()}",
        "description": (
            f"**AQI hiện tại:** {aqi_value}\n"
            f"**Mức:** {level}\n"
            f"**Thời gian:** {timestamp} (UTC)"
        ),
        "color": int(LEVEL_COLORS.get(level, "#808080").lstrip("#"), 16),
    }
    payload = {
        "content": f"@everyone 📊 Báo cáo AQI {city.title()}: hiện đang ở mức {level} ({aqi_value})",
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

    level_changed = last_record is not None and last_record[2] != new_level

    if level_changed:
        _, old_aqi, old_level = last_record
        print(f"Ngưỡng thay đổi: {old_level} -> {new_level}. Đang gửi cảnh báo Discord...")
        send_discord_alert(discord_webhook, old_aqi, old_level, aqi_value, new_level, timestamp, CITY)
        print("Đã gửi cảnh báo Discord thành công.")
    elif is_scheduled_report_window():
        print("Đang trong khung giờ báo cáo định kỳ (6h/12h/18h). Đang gửi báo cáo Discord...")
        send_discord_status_report(discord_webhook, aqi_value, new_level, timestamp, CITY)
        print("Đã gửi báo cáo Discord thành công.")
    elif last_record is None:
        print("Chưa có dữ liệu trước đó và không phải giờ báo cáo, bỏ qua (lần chạy đầu tiên).")
    else:
        print(f"Ngưỡng không đổi ({new_level}) và không phải giờ báo cáo, không gửi thông báo.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

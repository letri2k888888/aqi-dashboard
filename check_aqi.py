"""
check_aqi.py
------------
Script chạy định kỳ (qua GitHub Actions cron, mỗi 30 phút) để:
  1. Lấy dữ liệu AQI hiện tại từ AQICN API cho thành phố cấu hình sẵn.
  2. Phân loại AQI theo ngưỡng EPA.
  3. Lưu kết quả vào SQLite (bảng aqi_history).
  4. So sánh ngưỡng mới với ngưỡng đã lưu ở lần chạy trước đó.
     Nếu ngưỡng thay đổi (tăng hoặc giảm) -> gửi cảnh báo qua Discord Webhook.
     Nếu ngưỡng không đổi -> không gửi gì cả, tránh spam kênh Discord.

Biến môi trường bắt buộc:
  - AQICN_TOKEN     : API token lấy tại https://aqicn.org/data-platform/token/
  - DISCORD_WEBHOOK : URL webhook của kênh Discord muốn nhận cảnh báo

Biến môi trường tuỳ chọn:
  - AQI_CITY        : tên thành phố/trạm AQICN, mặc định "hanoi"
"""

import os
import sys

import requests

from aqi_common import CITY, LEVEL_COLORS, classify_aqi, get_db_connection, get_last_record, insert_record

AQICN_API_URL = "https://api.waqi.info/feed/{city}/?token={token}"

# Discord User ID để @mention trong tin nhắn cảnh báo — bắt buộc phải mention
# thì Discord mới đẩy push notification (banner/lock screen) trên điện thoại,
# nếu không tin nhắn webhook thường chỉ hiện badge, không bật thông báo ra ngoài.
DISCORD_MENTION_USER_ID = "945622381915951104"


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
        # Push notification trên điện thoại chỉ hiện được nội dung của "content",
        # KHÔNG hiện nội dung bên trong "embeds" -> phải nhét luôn thông tin tóm
        # tắt (AQI cũ->mới, ngưỡng cũ->mới) vào đây, không chỉ mỗi mention.
        "content": (
            f"<@{DISCORD_MENTION_USER_ID}> ⚠️ AQI {city.title()} đổi mức: "
            f"{old_level} ({old_aqi}) → {new_level} ({new_aqi})"
        ),
        "embeds": [embed],
        "allowed_mentions": {"users": [DISCORD_MENTION_USER_ID]},
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

    if last_record is None:
        print("Chưa có dữ liệu trước đó, bỏ qua bước so sánh (lần chạy đầu tiên).")
        return 0

    _, old_aqi, old_level = last_record

    if old_level != new_level:
        print(f"Ngưỡng thay đổi: {old_level} -> {new_level}. Đang gửi cảnh báo Discord...")
        send_discord_alert(discord_webhook, old_aqi, old_level, aqi_value, new_level, timestamp, CITY)
        print("Đã gửi cảnh báo Discord thành công.")
    else:
        print(f"Ngưỡng không đổi ({new_level}), không gửi thông báo.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Module dùng chung cho check_aqi.py và dashboard.py.
Chứa: cấu hình chung, hàm phân loại AQI theo ngưỡng EPA, và các hàm thao tác SQLite.
"""

import os
import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------

# Đường dẫn file SQLite (đặt trong thư mục db/ để dễ .gitignore riêng nếu cần,
# nhưng ở đây ta CHO PHÉP commit ngược file này để giữ lịch sử qua các lần
# chạy GitHub Actions, vì runner của Actions không có state cố định).
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "aqi_history.db")

# Thành phố / trạm quan trắc trên AQICN (https://aqicn.org/city/<ten-thanh-pho>)
# Có thể override bằng biến môi trường AQI_CITY, mặc định là Hà Nội.
CITY = os.environ.get("AQI_CITY", "hanoi")

# Ngưỡng phân loại AQI theo chuẩn EPA (Air Quality Index) — dùng breakpoint
# thấp nhất của mỗi khoảng để so sánh dạng "aqi >= threshold".
# Thứ tự từ cao xuống thấp để duyệt tìm ngưỡng phù hợp đầu tiên.
AQI_LEVELS = [
    (301, "Hazardous"),
    (201, "Very Unhealthy"),
    (151, "Unhealthy"),
    (101, "Unhealthy for Sensitive Groups"),
    (51, "Moderate"),
    (0, "Good"),
]

# Màu tương ứng mỗi ngưỡng (dùng cho Discord embed và cho dashboard Streamlit)
LEVEL_COLORS = {
    "Good": "#00E400",
    "Moderate": "#FFFF00",
    "Unhealthy for Sensitive Groups": "#FF7E00",
    "Unhealthy": "#FF0000",
    "Very Unhealthy": "#8F3F97",
    "Hazardous": "#7E0023",
}


def classify_aqi(aqi_value: int) -> str:
    """Trả về tên ngưỡng (level) tương ứng với giá trị AQI, theo chuẩn EPA."""
    for threshold, level_name in AQI_LEVELS:
        if aqi_value >= threshold:
            return level_name
    return "Good"  # fallback, không nên xảy ra vì threshold thấp nhất là 0


def get_db_connection() -> sqlite3.Connection:
    """Mở kết nối SQLite, tự tạo bảng aqi_history nếu chưa tồn tại."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aqi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            aqi_value INTEGER NOT NULL,
            level TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_last_record(conn: sqlite3.Connection):
    """Lấy bản ghi gần nhất (trước lần ghi hiện tại), dùng để so sánh ngưỡng.

    Trả về tuple (timestamp, aqi_value, level) hoặc None nếu bảng đang rỗng
    (tức đây là lần chạy đầu tiên).
    """
    cur = conn.execute(
        "SELECT timestamp, aqi_value, level FROM aqi_history ORDER BY id DESC LIMIT 1"
    )
    return cur.fetchone()


def insert_record(conn: sqlite3.Connection, aqi_value: int, level: str) -> str:
    """Ghi một bản ghi AQI mới vào SQLite, trả về timestamp (UTC, ISO 8601) đã dùng."""
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO aqi_history (timestamp, aqi_value, level) VALUES (?, ?, ?)",
        (timestamp, aqi_value, level),
    )
    conn.commit()
    return timestamp

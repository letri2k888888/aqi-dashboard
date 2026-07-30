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
# Dùng "or" thay vì giá trị mặc định của .get(): trên GitHub Actions, biến
# env luôn tồn tại (dù rỗng) khi repo chưa cấu hình vars.AQI_CITY, nên
# os.environ.get("AQI_CITY", "hanoi") sẽ trả về "" thay vì "hanoi".
CITY = os.environ.get("AQI_CITY") or "hanoi"

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


# ---------------------------------------------------------------------------
# Quy đổi nồng độ PM2.5/PM10 dự báo sang thang AQI (chuẩn EPA)
# ---------------------------------------------------------------------------
# AQICN trả dữ liệu dự báo dưới dạng nồng độ (µg/m³) theo từng chất riêng lẻ,
# KHÔNG có sẵn 1 con số AQI dự báo duy nhất — cần tự quy đổi bằng công thức
# breakpoint tuyến tính từng đoạn của EPA: mỗi tuple là
# (nồng độ thấp, nồng độ cao, AQI thấp, AQI cao) của 1 đoạn.

PM25_BREAKPOINTS = [
    (0.0, 12.0, 0, 50),
    (12.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 150.4, 151, 200),
    (150.5, 250.4, 201, 300),
    (250.5, 350.4, 301, 400),
    (350.5, 500.4, 401, 500),
]

PM10_BREAKPOINTS = [
    (0, 54, 0, 50),
    (55, 154, 51, 100),
    (155, 254, 101, 150),
    (255, 354, 151, 200),
    (355, 424, 201, 300),
    (425, 504, 301, 400),
    (505, 604, 401, 500),
]


def _concentration_to_aqi(concentration: float, breakpoints: list) -> int:
    """Áp công thức breakpoint tuyến tính của EPA để quy đổi 1 nồng độ (µg/m³)
    sang thang AQI (0-500)."""
    for c_low, c_high, i_low, i_high in breakpoints:
        if c_low <= concentration <= c_high:
            return round((i_high - i_low) / (c_high - c_low) * (concentration - c_low) + i_low)
    # Nồng độ vượt bảng breakpoint (ô nhiễm cực nặng) -> chốt ở mức AQI cao nhất.
    return breakpoints[-1][3]


def forecast_pm_to_aqi(pm25_value, pm10_value):
    """Quy đổi nồng độ PM2.5/PM10 dự báo (µg/m³) sang 1 giá trị AQI duy nhất.

    Lấy giá trị AQI cao hơn giữa 2 chất (giống cách AQICN tính AQI thật: lấy
    max của các chỉ số thành phần). Trả về None nếu không có dữ liệu nào.
    """
    candidates = []
    if pm25_value is not None:
        candidates.append(_concentration_to_aqi(pm25_value, PM25_BREAKPOINTS))
    if pm10_value is not None:
        candidates.append(_concentration_to_aqi(pm10_value, PM10_BREAKPOINTS))
    return max(candidates) if candidates else None


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


def get_record_near(conn: sqlite3.Connection, target_timestamp: str, tolerance_seconds: int = 3 * 3600):
    """Tìm bản ghi có timestamp GẦN target_timestamp nhất (chênh lệch tuyệt đối
    theo giây), dùng để so sánh AQI với "cùng giờ hôm qua".

    Trả về (timestamp, aqi_value, level), hoặc None nếu bảng rỗng hoặc bản ghi
    gần nhất vẫn cách target_timestamp quá xa (quá tolerance_seconds) — trường
    hợp này xảy ra khi hệ thống chưa đủ 1 ngày dữ liệu.
    """
    cur = conn.execute(
        """
        SELECT timestamp, aqi_value, level,
               ABS(strftime('%s', timestamp) - strftime('%s', ?)) AS diff_seconds
        FROM aqi_history
        ORDER BY diff_seconds ASC
        LIMIT 1
        """,
        (target_timestamp,),
    )
    row = cur.fetchone()
    if row is None or row[3] > tolerance_seconds:
        return None
    return row[0], row[1], row[2]


def insert_record(conn: sqlite3.Connection, aqi_value: int, level: str) -> str:
    """Ghi một bản ghi AQI mới vào SQLite, trả về timestamp (UTC, ISO 8601) đã dùng."""
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO aqi_history (timestamp, aqi_value, level) VALUES (?, ?, ?)",
        (timestamp, aqi_value, level),
    )
    conn.commit()
    return timestamp

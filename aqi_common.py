"""
Module dùng chung cho check_aqi.py và dashboard.py.
Chứa: cấu hình chung, hàm phân loại AQI theo ngưỡng EPA, và các hàm thao tác SQLite.
"""

import os
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Múi giờ Việt Nam — dữ liệu LƯU trong SQLite luôn ở UTC (thực hành chuẩn,
# tránh lỗi khi so sánh/tính toán), nhưng HIỂN THỊ cho người dùng thì đổi
# sang giờ Việt Nam cho dễ đọc (xem format_vn_time bên dưới).
VN_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

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

# Tên tiếng Việt hiển thị trong nội dung tin nhắn Discord. Chỉ dùng để hiển
# thị — dữ liệu lưu SQLite, classify_aqi() và LEVEL_COLORS vẫn dùng tên
# tiếng Anh chuẩn EPA ở AQI_LEVELS, không đổi để tránh ảnh hưởng logic gốc.
LEVEL_NAMES_VN = {
    "Good": "Tốt",
    "Moderate": "Trung bình",
    "Unhealthy for Sensitive Groups": "Kém",
    "Unhealthy": "Xấu",
    "Very Unhealthy": "Rất xấu",
    "Hazardous": "Nguy hại",
}


def level_vn(level_en: str) -> str:
    """Tên tiếng Việt của 1 mức AQI, dùng khi soạn nội dung tin nhắn Discord.
    Trả về nguyên tên tiếng Anh nếu không khớp key nào (phòng hờ)."""
    return LEVEL_NAMES_VN.get(level_en, level_en)


# Danh sách thành phố được check_aqi.py theo dõi (loop qua từng entry, mỗi
# thành phố độc lập: dữ liệu, bộ đếm lỗi, và thông báo Discord riêng — xem
# process_city() trong check_aqi.py). Thêm thành phố mới: thêm 1 entry vào
# đây, KHÔNG cần sửa code.
#   - aqicn_slug   : slug thành phố dùng khi gọi AQICN API
#                    (https://aqicn.org/city/<slug>)
#   - display_name : tên tiếng Việt hiển thị trong tin nhắn Discord
#   - role_id      : Discord Role ID để @mention đúng người đã chọn thành phố
#                    này (Server Settings > Roles > chuột phải role > Copy ID,
#                    cần bật Developer Mode trước). Để trống "" nếu role chưa
#                    tạo xong — hệ thống sẽ tự dùng @everyone thay thế cho tới
#                    khi bạn điền role_id vào, không bị lỗi hay ngừng chạy.
CITIES = {
    "hanoi": {
        "aqicn_slug": "hanoi",
        "display_name": "Hà Nội",
        "role_id": "1536932879387459665"
    },
}


def city_vn(city_slug: str) -> str:
    """Tên tiếng Việt của thành phố để hiển thị. Tra theo CITIES; nếu slug lạ
    (đổi AQI_CITY sang thành phố khác chưa khai báo trong CITIES), rơi về
    city.title() như cũ."""
    entry = CITIES.get(city_slug.lower())
    return entry["display_name"] if entry else city_slug.title()


def classify_aqi(aqi_value: int) -> str:
    """Trả về tên ngưỡng (level) tương ứng với giá trị AQI, theo chuẩn EPA."""
    for threshold, level_name in AQI_LEVELS:
        if aqi_value >= threshold:
            return level_name
    return "Good"  # fallback, không nên xảy ra vì threshold thấp nhất là 0


def format_vn_time(utc_iso_str: str) -> str:
    """Chuyển timestamp UTC ISO 8601 (định dạng lưu trong SQLite, ví dụ
    "2026-08-03T03:00:56+00:00") sang chuỗi hiển thị theo GIỜ VIỆT NAM, dễ
    đọc hơn cho người dùng cuối (ví dụ "2026-08-03 10:00:56")."""
    dt_utc = datetime.fromisoformat(utc_iso_str)
    dt_vn = dt_utc.astimezone(VN_TIMEZONE)
    return dt_vn.strftime("%Y-%m-%d %H:%M:%S")


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
    """Mở kết nối SQLite, tự tạo bảng aqi_history và system_status_by_city
    nếu chưa tồn tại (system_status_by_city dùng để theo dõi số lần lấy dữ
    liệu AQICN thất bại liên tiếp CHO TỪNG THÀNH PHỐ, phục vụ tính năng cảnh
    báo sự cố hệ thống)."""
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
    # Migration: thêm cột "city" cho DB tạo từ phiên bản code cũ (chỉ theo
    # dõi 1 thành phố duy nhất). ALTER TABLE lỗi nếu cột đã có sẵn -> bỏ qua.
    # DEFAULT 'hanoi' vì mọi bản ghi cũ trong DB đều thực chất là của Hà Nội.
    try:
        conn.execute("ALTER TABLE aqi_history ADD COLUMN city TEXT NOT NULL DEFAULT 'hanoi'")
    except sqlite3.OperationalError:
        pass

    # Bảng cũ (giữ nguyên, không xoá, để không mất dữ liệu cũ nếu có script
    # khác còn tham chiếu) — chỉ theo dõi ĐƯỢC 1 thành phố (1 dòng duy nhất,
    # id=1). Thay bằng system_status_by_city bên dưới để theo dõi độc lập
    # từng thành phố trong CITIES.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            failure_alert_sent INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO system_status (id, consecutive_failures, failure_alert_sent) VALUES (1, 0, 0)"
    )
    try:
        conn.execute("ALTER TABLE system_status ADD COLUMN last_report_marker TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_status_by_city (
            city TEXT PRIMARY KEY,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            failure_alert_sent INTEGER NOT NULL DEFAULT 0,
            last_report_marker TEXT NOT NULL DEFAULT ''
        )
        """
    )
    # Migrate 1 lần: đưa dữ liệu của bảng system_status cũ (vốn chỉ theo dõi
    # "hanoi") sang system_status_by_city, để không mất bộ đếm lỗi/report
    # marker đã tích luỹ từ trước khi có tính năng nhiều thành phố. INSERT OR
    # IGNORE nên chạy lại nhiều lần vẫn an toàn, không ghi đè dữ liệu mới hơn.
    old_row = conn.execute(
        "SELECT consecutive_failures, failure_alert_sent, last_report_marker FROM system_status WHERE id = 1"
    ).fetchone()
    if old_row:
        conn.execute(
            """
            INSERT OR IGNORE INTO system_status_by_city
                (city, consecutive_failures, failure_alert_sent, last_report_marker)
            VALUES ('hanoi', ?, ?, ?)
            """,
            old_row,
        )
    # Đảm bảo mọi thành phố trong CITIES đều có sẵn 1 dòng trong
    # system_status_by_city, để các hàm record_fetch_*/get_last_report_marker
    # phía dưới không cần tự lo việc tạo dòng mới (UPSERT) mỗi lần gọi.
    for city_slug in CITIES:
        conn.execute("INSERT OR IGNORE INTO system_status_by_city (city) VALUES (?)", (city_slug,))

    conn.commit()
    return conn


def get_last_record(conn: sqlite3.Connection, city: str):
    """Lấy bản ghi gần nhất CỦA THÀNH PHỐ NÀY (trước lần ghi hiện tại), dùng
    để so sánh ngưỡng.

    Trả về tuple (timestamp, aqi_value, level) hoặc None nếu thành phố này
    chưa có bản ghi nào (tức đây là lần chạy đầu tiên cho thành phố đó).
    """
    cur = conn.execute(
        "SELECT timestamp, aqi_value, level FROM aqi_history WHERE city = ? ORDER BY id DESC LIMIT 1",
        (city,),
    )
    return cur.fetchone()


def get_record_near(conn: sqlite3.Connection, target_timestamp: str, city: str, tolerance_seconds: int = 3 * 3600):
    """Tìm bản ghi CỦA THÀNH PHỐ NÀY có timestamp GẦN target_timestamp nhất
    (chênh lệch tuyệt đối theo giây), dùng để so sánh AQI với "cùng giờ hôm
    qua".

    Trả về (timestamp, aqi_value, level), hoặc None nếu thành phố chưa có dữ
    liệu hoặc bản ghi gần nhất vẫn cách target_timestamp quá xa (quá
    tolerance_seconds) — trường hợp này xảy ra khi hệ thống chưa đủ 1 ngày
    dữ liệu.
    """
    cur = conn.execute(
        """
        SELECT timestamp, aqi_value, level,
               ABS(strftime('%s', timestamp) - strftime('%s', ?)) AS diff_seconds
        FROM aqi_history
        WHERE city = ?
        ORDER BY diff_seconds ASC
        LIMIT 1
        """,
        (target_timestamp, city),
    )
    row = cur.fetchone()
    if row is None or row[3] > tolerance_seconds:
        return None
    return row[0], row[1], row[2]


def insert_record(conn: sqlite3.Connection, aqi_value: int, level: str, city: str) -> str:
    """Ghi một bản ghi AQI mới của 1 thành phố vào SQLite, trả về timestamp
    (UTC, ISO 8601) đã dùng."""
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO aqi_history (timestamp, aqi_value, level, city) VALUES (?, ?, ?, ?)",
        (timestamp, aqi_value, level, city),
    )
    conn.commit()
    return timestamp


# ---------------------------------------------------------------------------
# Theo dõi sự cố hệ thống (số lần lấy dữ liệu AQICN thất bại liên tiếp)
# ---------------------------------------------------------------------------

def record_fetch_success(conn: sqlite3.Connection, city: str) -> None:
    """Gọi khi lấy dữ liệu AQICN của 1 thành phố thành công — reset bộ đếm
    lỗi liên tiếp của thành phố đó về 0 và cho phép gửi cảnh báo mới nếu có
    đợt lỗi khác xảy ra sau này."""
    conn.execute(
        "UPDATE system_status_by_city SET consecutive_failures = 0, failure_alert_sent = 0 WHERE city = ?",
        (city,),
    )
    conn.commit()


def record_fetch_failure(conn: sqlite3.Connection, city: str) -> int:
    """Gọi khi lấy dữ liệu AQICN của 1 thành phố thất bại — tăng bộ đếm lỗi
    liên tiếp của thành phố đó lên 1, trả về giá trị mới."""
    conn.execute(
        "UPDATE system_status_by_city SET consecutive_failures = consecutive_failures + 1 WHERE city = ?",
        (city,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT consecutive_failures FROM system_status_by_city WHERE city = ?", (city,)
    ).fetchone()
    return row[0]


def has_sent_failure_alert(conn: sqlite3.Connection, city: str) -> bool:
    """True nếu đã gửi cảnh báo sự cố cho đợt lỗi liên tiếp hiện tại của
    thành phố này rồi — dùng để chỉ gửi 1 lần duy nhất mỗi đợt lỗi, tránh
    spam khi lỗi kéo dài."""
    row = conn.execute(
        "SELECT failure_alert_sent FROM system_status_by_city WHERE city = ?", (city,)
    ).fetchone()
    return bool(row[0]) if row else False


def mark_failure_alert_sent(conn: sqlite3.Connection, city: str) -> None:
    """Đánh dấu đã gửi cảnh báo cho đợt lỗi hiện tại của thành phố này."""
    conn.execute("UPDATE system_status_by_city SET failure_alert_sent = 1 WHERE city = ?", (city,))
    conn.commit()


def get_last_report_marker(conn: sqlite3.Connection, city: str) -> str:
    """Lấy "mã khung giờ" của báo cáo định kỳ gần nhất đã gửi thành công cho
    thành phố này (dạng "YYYY-MM-DD-H", ví dụ "2026-08-06-18") — dùng để
    chống gửi trùng khi có nhiều lần thử lại trong cùng 1 khung giờ (xem
    is_scheduled_report_window trong check_aqi.py)."""
    row = conn.execute(
        "SELECT last_report_marker FROM system_status_by_city WHERE city = ?", (city,)
    ).fetchone()
    return row[0] if row else ""


def set_last_report_marker(conn: sqlite3.Connection, city: str, marker: str) -> None:
    """Ghi lại mã khung giờ vừa gửi báo cáo định kỳ thành công cho thành phố này."""
    conn.execute(
        "UPDATE system_status_by_city SET last_report_marker = ? WHERE city = ?", (marker, city)
    )
    conn.commit()

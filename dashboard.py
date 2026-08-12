"""
dashboard.py
------------
Ứng dụng Streamlit chạy LOCAL để trực quan hoá dữ liệu AQI đã được
check_aqi.py thu thập và lưu vào SQLite (db/aqi_history.db).

Chạy bằng lệnh:
    streamlit run dashboard.py

Lưu ý: dashboard.py chỉ ĐỌC dữ liệu, không gọi AQICN API và không gửi
Discord — việc thu thập dữ liệu là trách nhiệm của check_aqi.py (chạy
qua GitHub Actions hoặc thủ công).
"""

import os
import subprocess

import pandas as pd
import streamlit as st

from aqi_common import AQI_LEVELS, CITY, DB_PATH, LEVEL_COLORS, VN_TIMEZONE, get_db_connection

st.set_page_config(page_title="Dashboard AQI", page_icon=":material/air:", layout="wide")

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


@st.cache_data(ttl="60s")
def load_history() -> pd.DataFrame:
    """Đọc lịch sử AQI của thành phố CITY từ SQLite. Cache 60 giây để tránh
    đọc file liên tục. Dùng get_db_connection() (thay vì tự mở kết nối) để
    luôn chạy qua migration schema mới nhất (vd. cột "city") trước khi đọc —
    quan trọng vì check_aqi.py giờ theo dõi nhiều thành phố (xem CITIES
    trong aqi_common.py), lọc theo city để biểu đồ không bị trộn dữ liệu
    giữa các thành phố khác nhau."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(columns=["timestamp", "aqi_value", "level"])

    with get_db_connection() as conn:
        df = pd.read_sql_query(
            "SELECT timestamp, aqi_value, level FROM aqi_history WHERE city = ? ORDER BY id",
            conn,
            params=(CITY,),
        )
    # Lưu trong SQLite là UTC (chuẩn) -> đổi sang giờ Việt Nam cho dễ đọc khi
    # hiển thị. Ảnh hưởng đồng bộ tới cả KPI, biểu đồ và bảng dữ liệu bên dưới
    # vì tất cả đều đọc chung cột "timestamp" này.
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(VN_TIMEZONE)
    return df


def pull_latest_data() -> None:
    """Kéo db mới nhất mà GitHub Actions đã commit lên repo về máy local.

    Dùng --ff-only: chỉ fast-forward, tuyệt đối không tạo merge commit hay
    ghi đè thay đổi cục bộ. Nếu có xung đột (vd. đang sửa dở code) hoặc
    không có mạng, lệnh tự thất bại êm - dashboard vẫn hiển thị dữ liệu cũ
    thay vì bị sập.
    """
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0 and "Already up to date" not in result.stdout:
            load_history.clear()
    except Exception:
        pass


st.title(f"Dashboard chất lượng không khí — {CITY.title()}")
st.caption(
    "Dữ liệu nguồn: AQICN API · Cập nhật tự động mỗi 30 phút qua GitHub Actions · "
    "Trang tự làm mới và tự git pull mỗi 60 giây"
)


# Bọc toàn bộ phần nội dung trong 1 fragment với run_every="60s" để Streamlit
# tự rerun riêng phần này mỗi 60 giây (không reload lại toàn trang), đồng bộ
# với ttl của cache load_history() bên trên -> luôn đọc lại DB mới nhất mà
# không cần người dùng bấm nút thủ công.
@st.fragment(run_every="60s")
def render_dashboard() -> None:
    pull_latest_data()

    if st.button("Làm mới dữ liệu", icon=":material/refresh:"):
        load_history.clear()
        pull_latest_data()

    df = load_history()

    if df.empty:
        st.info(
            "Chưa có dữ liệu. Hãy chạy `python check_aqi.py` (với AQICN_TOKEN và "
            "DISCORD_WEBHOOK đã cấu hình) hoặc đợi GitHub Actions chạy lần đầu."
        )
        st.stop()

    latest = df.iloc[-1]
    latest_color = LEVEL_COLORS.get(latest["level"], "#808080")

    # --- Hàng KPI: AQI hiện tại + ngưỡng hiện tại ---------------------------
    with st.container(horizontal=True):
        st.metric(
            "AQI hiện tại",
            int(latest["aqi_value"]),
            border=True,
            chart_data=df["aqi_value"].tolist()[-20:],
            chart_type="line",
        )
        with st.container(border=True):
            st.markdown("**Mức cảnh báo hiện tại**")
            st.markdown(
                f"<span style='color:{latest_color}; font-size:1.4rem; font-weight:600'>"
                f"{latest['level']}</span>",
                unsafe_allow_html=True,
            )
        with st.container(border=True):
            st.markdown("**Cập nhật lần cuối (giờ VN)**")
            st.markdown(f"{latest['timestamp']}")

    # --- Biểu đồ lịch sử AQI -------------------------------------------------
    with st.container(border=True):
        st.subheader("Lịch sử AQI theo thời gian")
        st.line_chart(df, x="timestamp", y="aqi_value")

    # --- Bảng ngưỡng tham chiếu EPA -------------------------------------------
    with st.container(border=True):
        st.subheader("Bảng ngưỡng phân loại AQI (chuẩn EPA)")
        ref_rows_asc = sorted(AQI_LEVELS, key=lambda item: item[0])  # tăng dần theo threshold
        upper_bounds = [low - 1 for low, _ in ref_rows_asc[1:]] + [500]
        ref_df = pd.DataFrame(
            [
                {"Khoảng AQI": f"{low}-{high}", "Mức": level}
                for (low, level), high in zip(ref_rows_asc, upper_bounds)
            ]
        )
        st.dataframe(ref_df, hide_index=True, width="stretch")

    # --- Bảng dữ liệu thô ------------------------------------------------------
    with st.container(border=True):
        st.subheader("Dữ liệu chi tiết")
        st.dataframe(
            df.sort_values("timestamp", ascending=False),
            hide_index=True,
            width="stretch",
        )


render_dashboard()

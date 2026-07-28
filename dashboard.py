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

import pandas as pd
import streamlit as st

from aqi_common import AQI_LEVELS, CITY, DB_PATH, LEVEL_COLORS

st.set_page_config(page_title="Dashboard AQI", page_icon=":material/air:", layout="wide")


@st.cache_data(ttl="60s")
def load_history() -> pd.DataFrame:
    """Đọc toàn bộ lịch sử AQI từ SQLite. Cache 60 giây để tránh đọc file liên tục."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(columns=["timestamp", "aqi_value", "level"])

    import sqlite3

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT timestamp, aqi_value, level FROM aqi_history ORDER BY id", conn
        )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


st.title(f"Dashboard chất lượng không khí — {CITY.title()}")
st.caption("Dữ liệu nguồn: AQICN API · Cập nhật tự động mỗi 30 phút qua GitHub Actions")

if st.button("Làm mới dữ liệu", icon=":material/refresh:"):
    load_history.clear()

df = load_history()

if df.empty:
    st.info(
        "Chưa có dữ liệu. Hãy chạy `python check_aqi.py` (với AQICN_TOKEN và "
        "DISCORD_WEBHOOK đã cấu hình) hoặc đợi GitHub Actions chạy lần đầu."
    )
    st.stop()

latest = df.iloc[-1]
latest_color = LEVEL_COLORS.get(latest["level"], "#808080")

# --- Hàng KPI: AQI hiện tại + ngưỡng hiện tại -------------------------------
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
        st.markdown("**Cập nhật lần cuối (UTC)**")
        st.markdown(f"{latest['timestamp']}")

# --- Biểu đồ lịch sử AQI ------------------------------------------------------
with st.container(border=True):
    st.subheader("Lịch sử AQI theo thời gian")
    st.line_chart(df, x="timestamp", y="aqi_value")

# --- Bảng ngưỡng tham chiếu EPA ----------------------------------------------
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

# --- Bảng dữ liệu thô ---------------------------------------------------------
with st.container(border=True):
    st.subheader("Dữ liệu chi tiết")
    st.dataframe(
        df.sort_values("timestamp", ascending=False),
        hide_index=True,
        width="stretch",
    )

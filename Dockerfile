# Dockerfile cho dashboard.py (Streamlit) — CHỈ đóng gói phần dashboard xem
# lịch sử AQI, KHÔNG bao gồm check_aqi.py (phần đó cố tình vẫn chạy trên
# GitHub Actions, không cần server riêng — xem README phần "Docker").

FROM python:3.11-slim

WORKDIR /app

# Cài dependencies trước để tận dụng cache layer của Docker (chỉ cài lại khi
# requirements.txt thay đổi, không phải mỗi lần sửa code).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code cần thiết cho dashboard — aqi_common.py là module dùng chung nên
# copy kèm; check_aqi.py KHÔNG copy vì container này không chạy nó.
COPY aqi_common.py dashboard.py ./

EXPOSE 8501

# --server.address=0.0.0.0 để truy cập được từ ngoài container (mặc định
# Streamlit chỉ bind localhost bên trong container, không tiếp cận được).
# --server.headless=true để không cố mở trình duyệt bên trong container.
ENTRYPOINT ["streamlit", "run", "dashboard.py", "--server.address=0.0.0.0", "--server.headless=true"]

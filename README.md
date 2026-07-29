# Dashboard theo dõi AQI + cảnh báo Discord

Project cá nhân: lấy dữ liệu chất lượng không khí (AQI) từ AQICN, lưu lịch sử vào
SQLite, tự động gửi cảnh báo Discord khi mức AQI đổi ngưỡng, và hiển thị dashboard
bằng Streamlit.

## Cấu trúc project

```
aqi-dashboard/
├── aqi_common.py              # Hàm & cấu hình dùng chung (phân loại AQI, SQLite)
├── check_aqi.py               # Chạy trong GitHub Actions: lấy AQI, lưu DB, gửi Discord
├── dashboard.py                # Chạy local bằng Streamlit: xem dashboard
├── requirements.txt
├── .env.example                # Mẫu biến môi trường cho local (không commit .env thật)
├── db/aqi_history.db           # SQLite, được tạo & cập nhật tự động
└── .github/workflows/check_aqi.yml   # Cron chạy check_aqi.py mỗi 30 phút
```

## 1. Lấy AQICN API token (miễn phí)

1. Vào https://aqicn.org/data-platform/token/
2. Điền email, chọn mục đích phi thương mại/học tập, submit.
3. Token sẽ được gửi qua email (dạng chuỗi ký tự, ví dụ `abcd1234...`).

## 2. Tạo Discord Webhook

1. Vào server Discord của bạn > **Server Settings > Integrations > Webhooks**.
2. **New Webhook** > chọn kênh muốn nhận cảnh báo > **Copy Webhook URL**.

## 3. Chạy thử ở local

```bash
cd aqi-dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Mở .env, điền AQICN_TOKEN và DISCORD_WEBHOOK thật vào

export $(grep -v '^#' .env | xargs)   # nạp biến môi trường từ .env vào shell
python check_aqi.py                    # chạy 1 lần: lấy AQI, lưu DB, gửi Discord nếu đổi ngưỡng

streamlit run dashboard.py             # mở dashboard xem lịch sử AQI
```

## 4. Đưa lên GitHub để chạy tự động (GitHub Actions)

```bash
git init
git add .
git commit -m "feat: khởi tạo dashboard AQI + cảnh báo Discord"
git branch -M main
git remote add origin <URL_REPO_GITHUB_CUA_BAN>
git push -u origin main
```

Sau đó vào repo trên GitHub:

1. **Settings > Secrets and variables > Actions > New repository secret**, thêm:
   - `AQICN_TOKEN` = token lấy ở bước 1
   - `DISCORD_WEBHOOK` = URL webhook ở bước 2
2. (Tuỳ chọn) Nếu muốn đổi thành phố khác Hà Nội: tạo thêm **Repository variable**
   tên `AQI_CITY` (ví dụ `ho-chi-minh-city`) trong tab **Variables** cùng trang.
3. Tin cảnh báo luôn `@everyone` (ping toàn bộ thành viên server, kể cả offline)
   để Discord đẩy push notification ra màn hình — ai vào server Discord chứa
   webhook là tự động nhận thông báo, không cần khai báo gì thêm.
4. Workflow `.github/workflows/check_aqi.yml` sẽ tự chạy mỗi 30 phút (cron UTC),
   kể cả khi máy cá nhân tắt. Có thể bấm chạy thử ngay qua tab **Actions >
   Kiểm tra AQI định kỳ > Run workflow**.
4. Sau mỗi lần chạy, workflow tự commit lại `db/aqi_history.db` vào repo để giữ
   lịch sử — vì vậy nhớ `git pull` trước khi chạy dashboard local để có dữ liệu
   mới nhất từ Actions.

## Logic gửi thông báo Discord

Mỗi lần chạy (mỗi 30 phút), script lưu 1 bản ghi AQI mới vào SQLite, sau đó
gửi Discord trong 2 trường hợp (ưu tiên theo thứ tự, không gửi trùng lặp):

1. **Đổi ngưỡng** — nếu **ngưỡng (level)** của lần chạy hiện tại khác với
   ngưỡng của bản ghi **ngay trước đó**, luôn gửi cảnh báo, bất kể giờ giấc.
   Nếu AQI dao động nhưng vẫn nằm trong cùng một ngưỡng (ví dụ 60 → 75, đều
   là "Moderate"), sẽ không có thông báo nào được gửi ở bước này.
2. **Báo cáo định kỳ** — nếu ngưỡng không đổi, nhưng thời điểm hiện tại (giờ
   Việt Nam) đang trong khung 30 phút đầu của mốc **6h / 12h / 18h**, vẫn gửi
   1 báo cáo hiện trạng AQI để có cập nhật đều đặn trong ngày dù không có
   biến động.

Ngoài 2 trường hợp trên, script không gửi gì cả, tránh spam kênh Discord.

## Ngưỡng phân loại AQI (chuẩn EPA)

| Khoảng AQI | Mức |
|---|---|
| 0-50 | Good |
| 51-100 | Moderate |
| 101-150 | Unhealthy for Sensitive Groups |
| 151-200 | Unhealthy |
| 201-300 | Very Unhealthy |
| 301-500 | Hazardous |

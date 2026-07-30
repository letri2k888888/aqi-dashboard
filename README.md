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

## Kích hoạt đúng giờ đáng tin cậy hơn (khuyến nghị)

**Vấn đề:** lịch `schedule` của GitHub Actions (free tier) **không đảm bảo
chạy đúng giờ** — có thể trễ vài chục phút đến vài tiếng khi hệ thống GitHub
tải cao, đặc biệt với repo ít hoạt động. Đây là giới hạn hạ tầng, không phải
lỗi code.

**Giải pháp:** dùng 1 dịch vụ cron miễn phí ở ngoài (ví dụ
[cron-job.org](https://cron-job.org)) gọi thẳng GitHub REST API để kích hoạt
workflow đúng lịch, thay vì phụ thuộc `schedule:` nội bộ của GitHub. Workflow
hiện tại **không cần sửa gì** vì đã có sẵn `workflow_dispatch`.

Các bước tự thực hiện (không thể làm thay vì cần đăng nhập tài khoản cá nhân):

1. Tạo GitHub token phạm vi hẹp: **github.com/settings/personal-access-tokens**
   > **Fine-grained tokens > Generate new token**
   - **Repository access**: chỉ chọn repo `aqi-dashboard` (không chọn "All repositories")
   - **Permissions > Actions**: chọn **Read and write**
   - Generate, rồi copy token (chỉ hiện 1 lần) — token này càng hẹp quyền
     càng an toàn vì sẽ được lưu ở dịch vụ bên thứ ba.
2. Đăng ký tài khoản miễn phí tại [cron-job.org](https://cron-job.org).
3. Tạo cronjob mới với cấu hình:
   - **URL**:
     `https://api.github.com/repos/letri2k888888/aqi-dashboard/actions/workflows/check_aqi.yml/dispatches`
   - **Method**: `POST`
   - **Headers**:
     - `Authorization: Bearer <token bước 1>`
     - `Accept: application/vnd.github+json`
     - `Content-Type: application/json`
   - **Body**: `{"ref":"main"}`
   - **Schedule**: mỗi 30 phút (cron-job.org kích hoạt đúng giờ, không bị trễ
     như GitHub free tier).
4. Lưu ý bảo mật: không chia sẻ token này, và nếu ngừng dùng cron-job.org thì
   vào lại trang token ở bước 1 để **Revoke**.

Có thể giữ nguyên `schedule:` trong workflow làm phương án dự phòng miễn phí —
2 cơ chế không xung đột, chỉ khiến có thêm vài lần chạy dư (vô hại vì logic
thông báo đã tự chống trùng lặp).

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

### Dự báo AQI ngày mai (21h hằng ngày)

Tách biệt hoàn toàn với logic chống spam ở trên (không tranh chấp, không thay
thế): vào khung 30 phút đầu của **21h giờ Việt Nam** mỗi ngày, script gọi thêm
dữ liệu dự báo PM2.5/PM10 của AQICN cho ngày mai, tự quy đổi sang thang AQI
bằng công thức breakpoint chuẩn EPA (hàm `forecast_pm_to_aqi` trong
`aqi_common.py`), rồi gửi 1 tin dự báo riêng. Nếu đúng lúc 21h mà AQI cũng vừa
đổi ngưỡng, cả 2 tin (cảnh báo đổi ngưỡng + dự báo ngày mai) đều được gửi vì
chúng mang thông tin khác nhau (hiện tại vs. tương lai) — đây là chủ đích,
không phải lỗi trùng lặp.

### So sánh với cùng giờ hôm qua

Mỗi tin cảnh báo đổi ngưỡng hoặc báo cáo định kỳ đều tự động kèm thêm 1 dòng
so sánh AQI hiện tại với bản ghi **gần "cùng giờ hôm qua" nhất** đã lưu trong
SQLite (hàm `get_record_near` trong `aqi_common.py`, dung sai ±3 giờ). Nếu hệ
thống chưa đủ dữ liệu (ví dụ mới triển khai chưa tới 1 ngày), dòng so sánh sẽ
tự động không hiện ra thay vì báo sai lệch. Tính năng này không cần gọi thêm
API nào — chỉ truy vấn lại dữ liệu lịch sử đã có sẵn.

## Ngưỡng phân loại AQI (chuẩn EPA)

| Khoảng AQI | Mức |
|---|---|
| 0-50 | Good |
| 51-100 | Moderate |
| 101-150 | Unhealthy for Sensitive Groups |
| 151-200 | Unhealthy |
| 201-300 | Very Unhealthy |
| 301-500 | Hazardous |

## Câu hỏi thường gặp (về vai trò của dashboard)

**Dashboard này để làm gì trong hệ thống?**
Là công cụ trực quan hoá lịch sử AQI đã thu thập (biểu đồ xu hướng, mức hiện
tại, bảng ngưỡng EPA), phục vụ theo dõi/phân tích. Kênh cảnh báo chính vẫn là
Discord — dashboard không thay thế vai trò đó.

**Sao không tích hợp cảnh báo luôn vào dashboard, đỡ cần Discord?**
Vì cảnh báo cần chủ động đẩy tin đến người dùng ngay khi có thay đổi (push
notification), kể cả khi không mở máy. Dashboard là bị động — phải tự mở mới
xem được, không phù hợp cho việc báo real-time.

**Sao chạy local mà không deploy public?**
Phạm vi là dự án cá nhân, mục tiêu là minh hoạ khả năng lưu trữ + trực quan
hoá dữ liệu, không cần phục vụ nhiều người dùng đồng thời. Deploy public
(Streamlit Cloud) là mở rộng có thể làm thêm, không bắt buộc cho lõi hệ thống.

**Dashboard lấy dữ liệu từ đâu, có tự động cập nhật không?**
Đọc trực tiếp từ file SQLite (`db/aqi_history.db`) do `check_aqi.py` ghi qua
GitHub Actions mỗi 30 phút. Dashboard chỉ đọc, không tự gọi API AQICN — có
cache 60 giây và nút "Làm mới" để đọc lại dữ liệu file.

**Sao dùng SQLite mà không dùng database khác?**
Quy mô dữ liệu nhỏ (1 giá trị/30 phút, 1 thành phố), không cần server
database riêng — SQLite là file đơn giản, đủ dùng, dễ triển khai và commit
ngược vào Git để giữ lịch sử qua GitHub Actions.

**Sao chọn Streamlit thay vì Flask/Django/React?**
Streamlit cho phép dựng giao diện trực quan nhanh bằng thuần Python, phù hợp
thời gian làm báo cáo cá nhân ngắn, không cần viết riêng HTML/CSS/JS.

**API key và Discord webhook được bảo vệ thế nào?**
Lưu trong biến môi trường/GitHub Secrets, không hard-code trong code; `.env`
bị `.gitignore` nên không lộ khi push lên GitHub.

**Có nhiều người mở dashboard cùng lúc bị lỗi không?**
Không, vì dashboard chỉ đọc (read-only) từ SQLite, không có thao tác ghi
đồng thời nên không xung đột.

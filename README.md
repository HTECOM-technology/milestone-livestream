# Milestone Livestream Project

Mô hình triển khai hiện tại:

```txt
User Internet
   ↓
Server Nginx Gateway
   ↓ proxy all requests
Host Windows - Nginx Local
   ├── /api/         → http://127.0.0.1:8000/api/
   ├── /hls/         → C:\hls\
   └── /thumbnails/  → C:\thumbnails\

Host Windows - Python Backend/Worker/FFmpeg
   ├── Connect/Login Milestone Mobile Server
   ├── RequestStream đúng 1 camera
   ├── Pipe JPEG frame → FFmpeg stdin
   ├── Write HLS → C:\hls\{camera_id}\index.m3u8
   └── Write thumbnail → C:\thumbnails\{camera_id}.jpg
```

## Nội dung chính

```txt
app/api/cameras.py                    API camera list, start, heartbeat, stop, status
app/services/camera_registry.py       Lấy camera thật từ GetAllViewsAndCameras
app/services/viewer_store.py          Quản lý viewer/session in-memory
app/worker_manager/manager.py         Mỗi camera active chỉ start 1 worker process
app/milestone/mobile_client.py        Connect/Login/RequestStream/CloseStream
app/milestone/jpeg_stream_reader.py   Tách JPEG frame từ stream /Video/{VideoId}
app/ffmpeg_runtime/hls_process.py     Pipe MJPEG vào FFmpeg và xuất HLS
app/worker_runtime/camera_worker.py   Worker livestream chính
app/worker_runtime/thumbnail_once.py  Refresh thumbnail camera inactive
nginx/windows_10_2_18_11_nginx.conf   Config Nginx Windows local
nginx/ubuntu_10_130_1_20_gateway.conf Config Nginx Ubuntu gateway
.env                                  Đã điền sẵn thông tin môi trường
```

## Cài trên Windows server - Host

```powershell
cd <project-folder>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
mkdir C:\hls -Force
mkdir C:\thumbnails -Force
```

File `.env` đã có sẵn:

```env
APP_PORT=8000
APP_RELOAD=false
PUBLIC_BASE_URL=<URL_NGINX_PROXY>
MILESTONE_BASE_URL=http://<IP_HOST_WINDOWS_SERVER>:8081
MILESTONE_LOGIN_TYPE=ActiveDirectory
MILESTONE_DOMAIN=VMS-ITS
MILESTONE_USERNAME=administrator
MILESTONE_PASSWORD=something
FFMPEG_BIN=C:\nginx\ffmpeg.exe
HLS_ROOT=C:\hls
THUMBNAIL_ROOT=C:\thumbnails
```

Để đăng nhập bằng **Milestone Basic user**, cấu hình:

```env
MILESTONE_LOGIN_TYPE=Basic
MILESTONE_USERNAME=<basic-user-name>
MILESTONE_PASSWORD=<basic-user-password>
```

Khi `MILESTONE_LOGIN_TYPE=Basic`, backend gửi `LoginType=Basic` và không ghép
`MILESTONE_DOMAIN` vào username. Với Windows/AD user, giữ
`MILESTONE_LOGIN_TYPE=ActiveDirectory` và cấu hình `MILESTONE_DOMAIN` như trên.

## Chạy Python backend

```powershell
python run.py
```

Backend mặc định listen local:

```txt
http://127.0.0.1:8000
```

### Chạy hai instance trên cùng một máy Windows

Có thể đặt mỗi instance ở một thư mục hoặc ổ đĩa riêng. Cấu hình `.env` của
từng instance với port khác nhau, ví dụ `APP_PORT=8000` và `APP_PORT=8001`, rồi
chạy `setup.bat start` trong từng thư mục.

Nếu hai instance có thể xử lý cùng camera, nên cấu hình `HLS_ROOT` và
`THUMBNAIL_ROOT` riêng để tránh ghi đè file của nhau, ví dụ:

```env
# Instance 1
APP_PORT=8000
HLS_ROOT=C:\hls-8000
THUMBNAIL_ROOT=C:\thumbnails-8000

# Instance 2 (trong file .env của clone thứ hai)
APP_PORT=8001
HLS_ROOT=C:\hls-8001
THUMBNAIL_ROOT=C:\thumbnails-8001
```

Các lệnh `start`, `status` và `stop` chỉ nhận diện process thuộc đúng thư mục
project hiện tại; PID còn sót lại do copy thư mục sẽ không bị nhận nhầm là app
của clone khác.

Vì Windows Nginx trên cùng server sẽ proxy `/api/` vào `127.0.0.1:8000`.

Test và reload:

```powershell
cd C:\nginx
.\nginx.exe -t
.\nginx.exe -s reload
```

Nếu Nginx chưa chạy:

```powershell
cd C:\nginx
.\nginx.exe
```

Health check:

```txt
GET /health
```

Endpoint này trả `200` khi backend connect/login được Milestone Mobile Server, và trả `503` nếu không kết nối hoặc đăng nhập được. Có thể chỉnh timeout bằng `MILESTONE_HEALTH_TIMEOUT_SECONDS`.

## API

```txt
GET  /health
GET  /api/cameras?refresh=true
POST /api/cameras/{camera_id}/watch/start
POST /api/cameras/{camera_id}/watch/heartbeat
POST /api/cameras/{camera_id}/watch/stop
GET  /api/cameras/{camera_id}/status
```

Heartbeat body:

```json
{"session_id":"..."}
```

## Dọn log tự động (00:00 hàng ngày)

`scripts/clear_old_logs.ps1` xoá file `*.log` quá 3 ngày ở 3 nơi:

- `<project>\logs\` — `app-<ts>.out.log`, `app-<ts>.err.log`, `supervisor.log`
- `<HLS_ROOT>\` — `thumbnail_refresh.log`
- `<HLS_ROOT>\<camera_id>\logs\` — `ffmpeg_stderr.log` của từng camera

Chỉ chạm file `*.log`; segment `.ts`, `index.m3u8`, `.jpg`, `worker_status.json`
và file `.pid` không bị ảnh hưởng. File đang bị process giữ handle được bỏ qua
và ghi vào `logs\clear_old_logs.log`, không làm task fail.

Đăng ký task (chạy PowerShell as Administrator, làm riêng trong từng thư mục
instance vì `HLS_ROOT` đọc từ `.env` của chính instance đó):

```powershell
cd C:\hld-its\hld_livestream
.\scripts\clear_old_logs.ps1 -Install

cd C:\cgnb-its\cgnb_livestream
.\scripts\clear_old_logs.ps1 -Install
```

Task name là `MilestoneLivestream-ClearLogs-<tên thư mục project>` nên hai
instance không đè lên nhau.

```powershell
.\scripts\clear_old_logs.ps1 -Days 3 -WhatIf   # chạy thử, chỉ in ra file sẽ xoá
.\scripts\clear_old_logs.ps1                   # dọn ngay, không cần task
.\scripts\clear_old_logs.ps1 -Status           # xem next run / last result
.\scripts\clear_old_logs.ps1 -Uninstall        # bỏ task
```

`supervisor.log` và `ffmpeg_stderr.log` được append liên tục nên theo
`LastWriteTime` chúng không bao giờ "quá 3 ngày". Vì vậy file `*.log` vượt
`-RotateOverMB` (mặc định 20 MB) sẽ được đổi tên thành `<tên>-<timestamp>.log`
để bắt đầu già đi rồi bị xoá ở các lần chạy sau. Đặt `-RotateOverMB 0` để tắt.

## Lưu ý vận hành

- Backend hiện dùng in-memory viewer/session/worker state.
- `APP_RELOAD` mặc định là `false` để tránh uvicorn reload tạo process con khó stop trên Windows server. Chỉ bật `APP_RELOAD=true` khi chạy dev thủ công.
- Có 2 Nginx chạy riêng biệt, 1 nginx nội bộ và 1 nginx public.
- Nginx Public chỉ làm gateway/proxy.
- Nginx nội bộ serve static HLS từ disk local, Python không serve file HLS cho user.
- Worker sẽ CloseStream khi stop/reconnect.
- FFmpeg cleanup segment cũ bằng `delete_segments+omit_endlist`.

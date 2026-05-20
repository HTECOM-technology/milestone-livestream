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
PUBLIC_BASE_URL=<URL_NGINX_PROXY>
MILESTONE_BASE_URL=http://<IP_HOST_WINDOWS_SERVER>:8081
MILESTONE_DOMAIN=VMS-ITS
MILESTONE_USERNAME=administrator
MILESTONE_PASSWORD=something
FFMPEG_BIN=C:\nginx\ffmpeg.exe
HLS_ROOT=C:\hls
THUMBNAIL_ROOT=C:\thumbnails
```

## Chạy Python backend

```powershell
python run.py
```

Backend mặc định listen local:

```txt
http://127.0.0.1:8000
```

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

## API

```txt
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

## Lưu ý vận hành

- Backend hiện dùng in-memory viewer/session/worker state.
- Có 2 Nginx chạy riêng biệt, 1 nginx nội bộ và 1 nginx public.
- Nginx Public chỉ làm gateway/proxy.
- Nginx nội bộ serve static HLS từ disk local, Python không serve file HLS cho user.
- Worker sẽ CloseStream khi stop/reconnect.
- FFmpeg cleanup segment cũ bằng `delete_segments+omit_endlist`.

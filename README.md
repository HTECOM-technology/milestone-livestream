# Milestone Livestream Project

Mô hình triển khai hiện tại:

```txt
User Internet
   ↓
10.130.1.20 - Ubuntu 22 Nginx Gateway
   ↓ proxy all requests
10.2.18.11 - Windows Nginx Local
   ├── /api/         → http://127.0.0.1:8000/api/
   ├── /hls/         → C:\hls\
   └── /thumbnails/  → C:\thumbnails\

10.2.18.11 - Python Backend/Worker/FFmpeg
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

## Cài trên Windows server 10.2.18.11

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
PUBLIC_BASE_URL=http://10.130.1.20
MILESTONE_BASE_URL=http://10.2.18.11:8081
MILESTONE_DOMAIN=VMS-ITS
MILESTONE_USERNAME=administrator
MILESTONE_PASSWORD=promise@123
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

## Cấu hình Windows Nginx trên 10.2.18.11

Copy nội dung file:

```txt
nginx/windows_10_2_18_11_nginx.conf
```

vào:

```txt
C:\nginx\conf\nginx.conf
```

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

Test local trên server 11:

```powershell
curl.exe http://127.0.0.1/health
curl.exe "http://127.0.0.1/api/cameras?refresh=true"
```

## Cấu hình Ubuntu Nginx gateway trên 10.130.1.20

Copy file:

```txt
nginx/ubuntu_10_130_1_20_gateway.conf
```

vào ví dụ:

```bash
sudo nano /etc/nginx/sites-available/milestone-gateway.conf
sudo ln -s /etc/nginx/sites-available/milestone-gateway.conf /etc/nginx/sites-enabled/milestone-gateway.conf
sudo nginx -t
sudo systemctl reload nginx
```

Gateway sẽ forward toàn bộ request về Windows Nginx:

```txt
http://10.2.18.11:80
```

## Test end-to-end qua gateway 20

Từ máy client hoặc server 20:

```powershell
curl.exe http://10.130.1.20/health
curl.exe "http://10.130.1.20/api/cameras?refresh=true"
```

Start xem camera test:

```powershell
curl.exe -X POST "http://10.130.1.20/api/cameras/66a23788-b7ef-45cf-9bb1-b154857a06b5/watch/start" -H "Content-Type: application/json" -d "{}"
```

Kiểm tra file trên server 11:

```powershell
dir C:\hls\66a23788-b7ef-45cf-9bb1-b154857a06b5
type C:\hls\66a23788-b7ef-45cf-9bb1-b154857a06b5\worker_status.json
dir C:\thumbnails
```

Mở HLS:

```txt
http://10.130.1.20/hls/66a23788-b7ef-45cf-9bb1-b154857a06b5/index.m3u8
```

Thumbnail:

```txt
http://10.130.1.20/thumbnails/66a23788-b7ef-45cf-9bb1-b154857a06b5.jpg
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

- Backend hiện dùng in-memory viewer/session/worker state, phù hợp chạy 1 instance Python trên server 11.
- Nginx 20 chỉ làm gateway/proxy.
- Nginx 11 serve static HLS từ disk local, Python không serve file HLS cho user.
- Worker sẽ CloseStream khi stop/reconnect.
- FFmpeg cleanup segment cũ bằng `delete_segments+omit_endlist`.
- File `.env` trong project này đang chứa password plaintext theo yêu cầu triển khai hiện tại.

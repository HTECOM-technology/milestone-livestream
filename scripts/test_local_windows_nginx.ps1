param(
  [string]$CameraId = "66a23788-b7ef-45cf-9bb1-b154857a06b5"
)

curl.exe "http://127.0.0.1/health"
curl.exe "http://127.0.0.1/api/cameras?refresh=true"
curl.exe "http://127.0.0.1/hls/$CameraId/index.m3u8"
curl.exe "http://127.0.0.1/thumbnails/$CameraId.jpg" --output "$env:TEMP\thumb_test.jpg"

param(
  [string]$CameraId = "66a23788-b7ef-45cf-9bb1-b154857a06b5"
)

$hlsDir = "C:\hls\$CameraId"
$thumb = "C:\thumbnails\$CameraId.jpg"

python -m app.worker_runtime.camera_worker `
  --camera-id $CameraId `
  --hls-dir $hlsDir `
  --thumbnail-path $thumb `
  --latest-path "$hlsDir\latest.jpg" `
  --status-path "$hlsDir\worker_status.json" `
  --log-dir "$hlsDir\logs"

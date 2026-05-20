param(
  [string]$BaseUrl = "http://10.130.1.20",
  [string]$CameraId = "66a23788-b7ef-45cf-9bb1-b154857a06b5"
)

curl.exe "$BaseUrl/health"
curl.exe "$BaseUrl/api/cameras?refresh=true"
curl.exe -X POST "$BaseUrl/api/cameras/$CameraId/watch/start" -H "Content-Type: application/json" -d "{}"
curl.exe "$BaseUrl/api/cameras/$CameraId/status"

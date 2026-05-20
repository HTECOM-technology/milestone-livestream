Topology final:

10.130.1.20 Ubuntu Nginx Gateway
- Receives user requests
- Proxies all traffic to 10.2.18.11:80

10.2.18.11 Windows Nginx + Python + FFmpeg
- Windows Nginx serves /hls/ from C:\hls
- Windows Nginx serves /thumbnails/ from C:\thumbnails
- Windows Nginx proxies /api/ to Python FastAPI at 127.0.0.1:8000
- Python backend manages viewer sessions and workers
- Python worker connects to Milestone Mobile Server and runs FFmpeg

Milestone Mobile Server:
- Configured in .env as MILESTONE_BASE_URL=http://10.2.18.16:8081

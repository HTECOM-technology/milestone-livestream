from pathlib import Path
from app.core.config import get_settings


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_hls_dir(camera_id: str) -> Path:
    settings = get_settings()
    return ensure_dir(Path(settings.hls_root) / camera_id)


def get_thumbnail_path(camera_id: str) -> Path:
    settings = get_settings()
    ensure_dir(settings.thumbnail_root)
    return Path(settings.thumbnail_root) / f"{camera_id}.jpg"


def build_hls_url(camera_id: str) -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}{settings.hls_public_prefix}/{camera_id}/index.m3u8"


def build_latest_url(camera_id: str) -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}{settings.hls_public_prefix}/{camera_id}/latest.jpg"


def build_thumbnail_url(camera_id: str) -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}{settings.thumbnail_public_prefix}/{camera_id}.jpg"

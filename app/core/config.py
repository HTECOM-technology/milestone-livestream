from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Milestone Fake Livestream Backend"
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_reload: bool = False

    public_base_url: str = "http://localhost"

    milestone_base_url: str = "http://10.2.18.11:8081"
    milestone_domain: str = "VMS-ITS"
    milestone_username: str = "administrator"
    milestone_password: str = ""
    milestone_verify_ssl: bool = False
    milestone_request_timeout_seconds: int = 20
    milestone_health_timeout_seconds: float = 5.0
    milestone_stream_timeout_seconds: int = 60
    milestone_reconnect_delay_seconds: int = 3
    milestone_camera_cache_ttl_seconds: int = 300

    hls_root: str = r"C:\hls"
    thumbnail_root: str = r"C:\thumbnails"
    hls_public_prefix: str = "/hls"
    thumbnail_public_prefix: str = "/thumbnails"

    worker_python_executable: str = "python"
    viewer_heartbeat_ttl_seconds: int = 90
    viewer_cleanup_interval_seconds: int = 10
    thumbnail_refresh_interval_minutes: int = 30

    ffmpeg_bin: str = r"C:\nginx\ffmpeg.exe"

    worker_target_fps: int = 2
    worker_frame_interval_seconds: float = 0.5
    worker_force_throttle: bool = False
    worker_idle_stop_delay_seconds: float = 30.0

    hls_width: int = 854
    hls_height: int = 480
    hls_bitrate: str = "300k"
    hls_maxrate: str = "400k"
    hls_bufsize: str = "800k"
    hls_segment_seconds: int = 1
    hls_list_size: int = 5
    hls_delete_threshold: int = 2
    hls_ready_timeout_seconds: float = 15.0
    hls_ready_poll_interval_seconds: float = 0.25

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

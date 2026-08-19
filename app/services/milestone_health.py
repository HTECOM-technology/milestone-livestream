import threading
import time

from app.core.config import get_settings
from app.milestone.mobile_client import MilestoneMobileClient

# Serialize + cache health probe: nhiều request /health đồng thời hoặc dồn dập
# không được biến thành nhiều lần login vào Milestone.
_lock = threading.Lock()
_cached_result: dict | None = None
_cached_at: float = 0.0


def _probe() -> dict:
    settings = get_settings()
    started_at = time.monotonic()
    client = MilestoneMobileClient(
        request_timeout_seconds=settings.milestone_health_timeout_seconds
    )
    logged_out = False

    try:
        client.connect_and_login()
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        return {
            "status": "error",
            "base_url": settings.milestone_base_url.rstrip("/"),
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }
    else:
        # Health check chỉ cần xác nhận Connect + LogIn chạy được, nên LogOut ngay
        # để Mobile Server nhả session thay vì chờ timeout ("User Timed Out" trong log).
        logged_out = client.logout()
    finally:
        client.close()

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    return {
        "status": "ok",
        "base_url": settings.milestone_base_url.rstrip("/"),
        "elapsed_ms": elapsed_ms,
        "logged_out": logged_out,
    }


def check_milestone_mobile_health() -> dict:
    global _cached_result, _cached_at

    ttl = get_settings().milestone_health_cache_seconds

    with _lock:
        if (
            ttl > 0
            and _cached_result is not None
            and time.monotonic() - _cached_at < ttl
        ):
            return {**_cached_result, "cached": True}

        result = _probe()

        # Chỉ cache kết quả ok: khi Milestone lỗi thì lần probe sau phải thật,
        # để /health phản ánh đúng lúc server hồi phục.
        if result["status"] == "ok":
            _cached_result = result
            _cached_at = time.monotonic()
        else:
            _cached_result = None

        return {**result, "cached": False}


def reset_milestone_health_cache() -> None:
    global _cached_result, _cached_at

    with _lock:
        _cached_result = None
        _cached_at = 0.0

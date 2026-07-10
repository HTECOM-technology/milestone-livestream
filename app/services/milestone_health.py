import time

from app.core.config import get_settings
from app.milestone.mobile_client import MilestoneMobileClient


def check_milestone_mobile_health() -> dict:
    settings = get_settings()
    started_at = time.monotonic()
    client = MilestoneMobileClient(
        request_timeout_seconds=settings.milestone_health_timeout_seconds
    )

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
    finally:
        client.close()

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    return {
        "status": "ok",
        "base_url": settings.milestone_base_url.rstrip("/"),
        "elapsed_ms": elapsed_ms,
    }

import json
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.services.camera_types import CameraDefinition


class CameraCache:
    def __init__(self, cache_file: str = "cache/cameras.json", ttl_hours: int = 24):
        self.cache_file = Path(cache_file)
        self.ttl = timedelta(hours=ttl_hours)
        self._lock = threading.RLock()
        self._memory: list[CameraDefinition] | None = None
        self._last_refresh_at: datetime | None = None

    def get(self) -> Optional[list[CameraDefinition]]:
        with self._lock:
            if self._memory and self._last_refresh_at:
                if datetime.now(timezone.utc) - self._last_refresh_at < self.ttl:
                    return self._memory

            return self._load_file_cache()

    def set(self, cameras: list[CameraDefinition]) -> None:
        with self._lock:
            self._memory = cameras
            self._last_refresh_at = datetime.now(timezone.utc)

            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "refreshed_at": self._last_refresh_at.isoformat(),
                "items": [asdict(c) for c in cameras],
            }

            self.cache_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def clear(self) -> None:
        with self._lock:
            self._memory = None
            self._last_refresh_at = None

            if self.cache_file.exists():
                self.cache_file.unlink()

    def _load_file_cache(self) -> Optional[list[CameraDefinition]]:
        if not self.cache_file.exists():
            return None

        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            refreshed_at = datetime.fromisoformat(payload["refreshed_at"])

            if datetime.now(timezone.utc) - refreshed_at > self.ttl:
                return None

            cameras = [
                CameraDefinition(
                    camera_id=item["camera_id"],
                    name=item["name"],
                    description=item.get("description"),
                )
                for item in payload["items"]
            ]

            self._memory = cameras
            self._last_refresh_at = refreshed_at
            return cameras

        except Exception:
            return None


camera_cache = CameraCache(ttl_hours=24)

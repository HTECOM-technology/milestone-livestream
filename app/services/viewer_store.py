from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4


@dataclass
class ViewerSession:
    session_id: str
    camera_id: str
    created_at: datetime
    last_heartbeat_at: datetime


class ViewerStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, ViewerSession] = {}

    def start_session(self, camera_id: str, session_id: str | None = None) -> ViewerSession:
        now = datetime.now(timezone.utc)
        sid = session_id or str(uuid4())
        with self._lock:
            session = ViewerSession(sid, camera_id, now, now)
            self._sessions[sid] = session
            return session

    def heartbeat(self, camera_id: str, session_id: str) -> ViewerSession | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.camera_id != camera_id:
                return None
            session.last_heartbeat_at = now
            return session

    def stop_session(self, camera_id: str, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.camera_id != camera_id:
                return False
            del self._sessions[session_id]
            return True

    def count_viewers(self, camera_id: str) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.camera_id == camera_id)

    def remove_stale_sessions(self, ttl_seconds: int) -> list[ViewerSession]:
        now = datetime.now(timezone.utc)
        expired_before = now - timedelta(seconds=ttl_seconds)
        removed: list[ViewerSession] = []
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.last_heartbeat_at < expired_before:
                    removed.append(session)
                    del self._sessions[session_id]
        return removed


viewer_store = ViewerStore()

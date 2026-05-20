import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Timer
from typing import Optional

from app.core.config import get_settings
from app.utils.paths import get_hls_dir, get_thumbnail_path


@dataclass
class WorkerRuntimeState:
    camera_id: str
    process: subprocess.Popen | None
    started_at: datetime
    status: str = 'starting'

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)


class CameraWorkerManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._workers: dict[str, WorkerRuntimeState] = {}
        self._idle_stop_timers: dict[str, Timer] = {}

    def start_worker(self, camera_id: str) -> WorkerRuntimeState:
        with self._lock:
            self.cancel_scheduled_stop(camera_id)
            existing = self._workers.get(camera_id)
            if existing and existing.is_running():
                existing.status = 'running'
                return existing

            hls_dir = get_hls_dir(camera_id)
            thumbnail_path = get_thumbnail_path(camera_id)
            latest_path = hls_dir / 'latest.jpg'
            status_path = hls_dir / 'worker_status.json'
            log_dir = hls_dir / 'logs'
            settings = get_settings()

            cmd = [
                settings.worker_python_executable or sys.executable,
                '-m', 'app.worker_runtime.camera_worker',
                '--camera-id', camera_id,
                '--hls-dir', str(hls_dir),
                '--thumbnail-path', str(thumbnail_path),
                '--latest-path', str(latest_path),
                '--status-path', str(status_path),
                '--log-dir', str(log_dir),
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path.cwd()),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            )

            state = WorkerRuntimeState(camera_id, process, datetime.now(timezone.utc), 'running')
            self._workers[camera_id] = state
            return state

    def stop_worker(self, camera_id: str) -> bool:
        with self._lock:
            self.cancel_scheduled_stop(camera_id)
            state = self._workers.get(camera_id)
            if not state:
                return False
            process = state.process
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                except Exception:
                    if process.poll() is None:
                        process.kill()
            self._workers.pop(camera_id, None)
            return True

    def schedule_stop_worker(self, camera_id: str, delay_seconds: float) -> bool:
        with self._lock:
            self.cancel_scheduled_stop(camera_id)
            state = self._workers.get(camera_id)
            if not state or not state.is_running():
                return False

            state.status = 'idle'
            timer = Timer(delay_seconds, self.stop_worker, args=[camera_id])
            timer.daemon = True
            self._idle_stop_timers[camera_id] = timer
            timer.start()
            return True

    def cancel_scheduled_stop(self, camera_id: str) -> None:
        timer = self._idle_stop_timers.pop(camera_id, None)
        if timer:
            timer.cancel()

    def get_worker(self, camera_id: str) -> Optional[WorkerRuntimeState]:
        with self._lock:
            state = self._workers.get(camera_id)
            if not state:
                return None
            state.status = 'running' if state.is_running() else 'exited'
            return state

    def is_active(self, camera_id: str) -> bool:
        state = self.get_worker(camera_id)
        return bool(state and state.is_running())

    def list_active_camera_ids(self) -> list[str]:
        with self._lock:
            return [cid for cid, state in self._workers.items() if state.is_running()]

    def read_worker_status(self, camera_id: str) -> dict | None:
        status_path = get_hls_dir(camera_id) / 'worker_status.json'
        if not status_path.exists():
            return None
        try:
            return json.loads(status_path.read_text(encoding='utf-8'))
        except Exception:
            return None

    def refresh_thumbnail_once(self, camera_id: str) -> None:
        settings = get_settings()
        thumbnail_path = get_thumbnail_path(camera_id)
        cmd = [
            settings.worker_python_executable or sys.executable,
            '-m', 'app.worker_runtime.thumbnail_once',
            '--camera-id', camera_id,
            '--thumbnail-path', str(thumbnail_path),
        ]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path.cwd()),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
        )


camera_worker_manager = CameraWorkerManager()

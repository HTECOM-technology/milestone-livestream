import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Timer
from typing import Optional

from app.core.config import get_settings
from app.ffmpeg_runtime.hls_process import kill_orphan_ffmpeg
from app.utils.paths import get_hls_dir, get_thumbnail_path

logger = logging.getLogger(__name__)

# Thời gian cho worker chạy hết khối finally (CloseStream + LogOut + stop ffmpeg)
# sau khi nhận tín hiệu dừng, trước khi bị kill cứng.
GRACEFUL_STOP_TIMEOUT_SECONDS = 12.0


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

            # Phiên trước có thể để lại ffmpeg orphan (worker bị kill cứng), nó vẫn
            # ghi segment vào hls_dir và giữ handle file.
            try:
                killed = kill_orphan_ffmpeg(hls_dir)
                if killed:
                    logger.warning(
                        'start_worker %s: đã giết %d ffmpeg orphan còn sót',
                        camera_id,
                        killed,
                    )
            except Exception:
                logger.exception('start_worker %s: dọn ffmpeg orphan thất bại', camera_id)

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

            # Không DEVNULL nữa: trước đây mọi traceback của worker bị ném đi, nên
            # không có cách nào biết vì sao một phiên stream chết.
            log_file = self._open_worker_log(log_dir)

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=log_file or subprocess.DEVNULL,
                    stderr=subprocess.STDOUT if log_file else subprocess.DEVNULL,
                    cwd=str(Path.cwd()),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
                )
            finally:
                if log_file:
                    log_file.close()

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
                self._stop_process_gracefully(camera_id, process)
            self._workers.pop(camera_id, None)
            return True

    def _open_worker_log(self, log_dir: Path):
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            return open(log_dir / 'worker.err.log', 'a', encoding='utf-8', errors='replace')
        except Exception:
            logger.exception('Không mở được worker.err.log trong %s', log_dir)
            return None

    def _stop_process_gracefully(self, camera_id: str, process: subprocess.Popen) -> None:
        """
        Cho worker cơ hội chạy hết khối finally: CloseStream, stop ffmpeg, LogOut.

        Trên Windows, terminate() là TerminateProcess nên handler SIGTERM của worker
        không bao giờ chạy, và mỗi lần stop là một session Milestone bị bỏ lại cho
        server tự thu hồi bằng timeout ("User Timed Out" trong log Mobile Server).
        Vì worker được spawn với CREATE_NEW_PROCESS_GROUP, pid của nó cũng là id của
        process group, nên CTRL_BREAK_EVENT tới đúng một mình nó.
        """
        if not self._signal_graceful_stop(camera_id, process):
            self._force_kill(process)
            return

        try:
            process.wait(timeout=GRACEFUL_STOP_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            logger.warning(
                'stop_worker %s: worker không tự thoát trong %.0fs, phải kill cứng '
                '(session Milestone có thể bị bỏ lại)',
                camera_id,
                GRACEFUL_STOP_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception('stop_worker %s: lỗi khi chờ worker thoát', camera_id)

        self._force_kill(process)

    def _signal_graceful_stop(self, camera_id: str, process: subprocess.Popen) -> bool:
        try:
            if sys.platform == 'win32':
                os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            return True
        except Exception:
            logger.exception(
                'stop_worker %s: không gửi được tín hiệu dừng cho pid %s',
                camera_id,
                process.pid,
            )
            return False

    def _force_kill(self, process: subprocess.Popen) -> None:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        except Exception:
            pass

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
        self.refresh_thumbnails_once([camera_id])

    def refresh_thumbnails_once(self, camera_ids: list[str]) -> bool:
        """
        Refresh thumbnail cho nhiều camera bằng MỘT subprocess, tức một lần login duy nhất.
        Trước đây mỗi camera là một subprocess => N login vào Milestone mỗi vòng job.
        """
        if not camera_ids:
            return False

        settings = get_settings()
        log_path = Path(settings.hls_root) / 'thumbnail_refresh.log'

        cmd = [
            settings.worker_python_executable or sys.executable,
            '-m', 'app.worker_runtime.thumbnail_once',
        ]
        for camera_id in camera_ids:
            cmd += ['--camera-id', camera_id]

        # Không nuốt output của subprocess: lỗi từng camera phải đọc được trong file log.
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, 'a', encoding='utf-8')
        except Exception:
            log_file = None

        try:
            subprocess.Popen(
                cmd,
                stdout=log_file or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log_file else subprocess.DEVNULL,
                cwd=str(Path.cwd()),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            )
        finally:
            if log_file:
                log_file.close()

        return True


camera_worker_manager = CameraWorkerManager()

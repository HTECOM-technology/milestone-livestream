import subprocess
import sys
from pathlib import Path
from typing import Optional

from app.core.config import get_settings


class FFmpegHLSProcess:
    def __init__(self, camera_id: str, hls_dir: str | Path, log_dir: str | Path | None = None) -> None:
        self.camera_id = camera_id
        self.hls_dir = Path(hls_dir)
        self.log_dir = Path(log_dir) if log_dir else self.hls_dir
        self.process: Optional[subprocess.Popen] = None
        self.stderr_file = None

    def start(self) -> None:
        settings = get_settings()

        self.hls_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_hls_files()

        segment_pattern = str(self.hls_dir / "seg_%05d.ts")
        index_path = str(self.hls_dir / "index.m3u8")
        vf_scale = f"scale={settings.hls_width}:{settings.hls_height}"

        cmd = [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "warning",
            "-f", "mjpeg",
            "-framerate", str(settings.worker_target_fps),
            "-i", "pipe:0",
            "-vf", vf_scale,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.worker_target_fps),
            "-g", str(settings.worker_target_fps * 2),
            "-b:v", settings.hls_bitrate,
            "-maxrate", settings.hls_maxrate,
            "-bufsize", settings.hls_bufsize,
            "-hls_time", str(settings.hls_segment_seconds),
            "-hls_list_size", str(settings.hls_list_size),
            "-hls_flags", "delete_segments+omit_endlist",
            "-hls_delete_threshold", str(settings.hls_delete_threshold),
            "-hls_segment_filename", segment_pattern,
            index_path,
        ]

        self.stderr_file = open(self.log_dir / "ffmpeg_stderr.log", "ab", buffering=0)

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr_file,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )

    def write_jpeg(self, jpeg_bytes: bytes) -> None:
        if not self.process:
            raise RuntimeError("FFmpeg process is not started")

        if self.process.poll() is not None:
            raise RuntimeError(f"FFmpeg exited with code {self.process.returncode}")

        if not self.process.stdin:
            raise RuntimeError("FFmpeg stdin is not available")

        self.process.stdin.write(jpeg_bytes)
        self.process.stdin.flush()

    def stop(self) -> None:
        if not self.process:
            self._close_stderr()
            return

        try:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except Exception:
                    pass

            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)

        except subprocess.TimeoutExpired:
            self.process.kill()

        except Exception:
            if self.process.poll() is None:
                self.process.kill()

        finally:
            self._close_stderr()

    def _cleanup_old_hls_files(self) -> None:
        if not self.hls_dir.exists():
            return

        for pattern in ("*.ts", "*.m3u8", "*.tmp"):
            for path in self.hls_dir.glob(pattern):
                try:
                    path.unlink()
                except Exception:
                    pass

    def _close_stderr(self) -> None:
        if self.stderr_file:
            try:
                self.stderr_file.close()
            except Exception:
                pass
            self.stderr_file = None

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

PID_FILE_NAME = "ffmpeg.pid"


def _ffmpeg_image_name() -> str:
    # Tách tay theo cả hai loại separator: pathlib.Path chỉ hiểu "\\" khi chính nó
    # đang chạy trên Windows, nên FFMPEG_BIN kiểu "C:\\...\\ffmpeg.exe" sẽ không
    # tách được nếu code này chạy ở nơi khác.
    raw = get_settings().ffmpeg_bin or "ffmpeg"
    return raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "ffmpeg"


def _is_ffmpeg_process(pid: int) -> bool:
    """
    Kiểm tra pid còn sống VÀ đúng là ffmpeg. Chỉ check pid còn sống là không đủ:
    Windows tái sử dụng pid, kill mù có thể giết process hoàn toàn khác.
    """
    image_name = _ffmpeg_image_name()

    if sys.platform == "win32":
        try:
            output = subprocess.run(
                [
                    "tasklist",
                    "/FI", f"PID eq {pid}",
                    "/FI", f"IMAGENAME eq {image_name}",
                    "/NH",
                    "/FO", "CSV",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except Exception:
            return False

        return image_name.lower() in output.lower()

    try:
        output = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return False

    actual = output.strip().replace("\\", "/").rsplit("/", 1)[-1]
    expected_stem = image_name.rsplit(".", 1)[0]
    return bool(actual) and actual.startswith(expected_stem)


def kill_orphan_ffmpeg(hls_dir: str | Path) -> int:
    """
    Giết ffmpeg còn sót của camera này rồi xoá pid file. Orphan xuất hiện khi worker
    bị kill cứng: worker chết nhưng ffmpeg con vẫn giữ handle vào thư mục HLS và tiếp tục
    ghi segment. Trả về số process đã giết.
    """
    pid_file = Path(hls_dir) / PID_FILE_NAME

    if not pid_file.exists():
        return 0

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return 0

    killed = 0

    if _is_ffmpeg_process(pid):
        try:
            if sys.platform == "win32":
                # /T để giết cả cây con, /F vì ffmpeg không có message loop để nhận
                # tín hiệu đóng nhẹ nhàng khi đã mất cha.
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            killed = 1
        except Exception:
            killed = 0

    pid_file.unlink(missing_ok=True)
    return killed


class FFmpegHLSProcess:
    def __init__(self, camera_id: str, hls_dir: str | Path, log_dir: str | Path | None = None) -> None:
        self.camera_id = camera_id
        self.hls_dir = Path(hls_dir)
        self.log_dir = Path(log_dir) if log_dir else self.hls_dir
        self.process: Optional[subprocess.Popen] = None
        self.stderr_file = None
        self.pid_file = self.hls_dir / PID_FILE_NAME

    def start(self) -> None:
        settings = get_settings()

        self.hls_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Phiên trước có thể đã bị kill cứng và để lại ffmpeg đang giữ file trong
        # hls_dir; phải dọn trước khi xoá segment cũ, không thì xoá không được.
        kill_orphan_ffmpeg(self.hls_dir)

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
            "-g", str(max(1, settings.worker_target_fps * settings.hls_segment_seconds)),
            "-keyint_min", str(max(1, settings.worker_target_fps * settings.hls_segment_seconds)),
            "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{settings.hls_segment_seconds})",
            "-b:v", settings.hls_bitrate,
            "-maxrate", settings.hls_maxrate,
            "-bufsize", settings.hls_bufsize,
            "-hls_time", str(settings.hls_segment_seconds),
            "-hls_list_size", str(settings.hls_list_size),
            "-hls_flags", "delete_segments+omit_endlist+independent_segments+temp_file",
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

        # Ghi pid ra file để lần start sau còn tìm được orphan nếu worker bị kill cứng.
        try:
            self.pid_file.write_text(str(self.process.pid), encoding="utf-8")
        except Exception:
            pass

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
            self.pid_file.unlink(missing_ok=True)
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
            try:
                self.pid_file.unlink(missing_ok=True)
            except Exception:
                pass

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

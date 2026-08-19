import argparse
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.ffmpeg_runtime.hls_process import FFmpegHLSProcess
from app.milestone.jpeg_stream_reader import MilestoneJPEGStreamReader
from app.milestone.mobile_client import MilestoneMobileClient

logger = logging.getLogger(__name__)

running = True

# Response video đang mở của phiên hiện tại, để đánh thức reader khi cần dừng.
_active_response = None

# Sau khi nhận tín hiệu dừng, chờ bấy nhiêu giây cho vòng lặp tự thoát ở frame kế
# tiếp (2 fps => thường dưới 0.5s). Chỉ khi stream đã tắc thì mới đóng socket, vì
# đóng socket trước CloseStream làm Mobile Server ghi HttpListenerException.
# Vẫn phải nhỏ hơn GRACEFUL_STOP_TIMEOUT_SECONDS của manager để kịp dọn dẹp.
FORCE_CLOSE_STREAM_AFTER_SECONDS = 2.0


def _force_close_active_response() -> None:
    response = _active_response
    if response is None:
        return

    try:
        response.close()
    except Exception:
        pass


def handle_stop(signum, frame):
    global running
    running = False

    if _active_response is None:
        return

    # Reader có thể đang block trong iter_content của một stream đã chết; không đánh
    # thức thì manager hết 12s sẽ kill cứng và session Milestone lại bị bỏ lại.
    timer = threading.Timer(FORCE_CLOSE_STREAM_AFTER_SECONDS, _force_close_active_response)
    timer.daemon = True
    timer.start()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, path)


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def update_status(
    status_path: Path,
    *,
    camera_id: str,
    status: str,
    message: Optional[str] = None,
    video_id: Optional[str] = None,
    stream_id: Optional[str] = None,
    frame_count: Optional[int] = None,
    last_frame_at: Optional[str] = None,
) -> None:
    payload = {
        "camera_id": camera_id,
        "status": status,
        "updated_at": utc_now(),
    }

    if message is not None:
        payload["message"] = message

    if video_id is not None:
        payload["video_id"] = video_id

    if stream_id is not None:
        payload["stream_id"] = stream_id

    if frame_count is not None:
        payload["frame_count"] = frame_count

    if last_frame_at is not None:
        payload["last_frame_at"] = last_frame_at

    atomic_write_json(status_path, payload)


def run_once(
    *,
    camera_id: str,
    hls_dir: Path,
    thumbnail_path: Path,
    latest_path: Path,
    status_path: Path,
    log_dir: Path,
) -> int:
    """Chạy một phiên stream. Trả về số frame đã đọc được trong phiên đó."""
    global _active_response

    settings = get_settings()

    client: Optional[MilestoneMobileClient] = None
    ffmpeg: Optional[FFmpegHLSProcess] = None
    response = None
    video_id: Optional[str] = None
    stream_id: Optional[str] = None
    frame_count = 0

    try:
        update_status(status_path, camera_id=camera_id, status="connecting")

        client = MilestoneMobileClient()
        client.connect_and_login()

        update_status(status_path, camera_id=camera_id, status="requesting_stream")

        stream_result = client.request_stream(
            camera_id=camera_id,
            fps=settings.worker_target_fps,
            width=settings.hls_width,
            height=settings.hls_height,
        )

        video_id = stream_result.video_id
        stream_id = stream_result.stream_id

        update_status(
            status_path,
            camera_id=camera_id,
            status="opening_video_stream",
            video_id=video_id,
            stream_id=stream_id,
        )

        response = client.open_video_stream(video_id)
        _active_response = response
        reader = MilestoneJPEGStreamReader(response)

        ffmpeg = FFmpegHLSProcess(camera_id=camera_id, hls_dir=hls_dir, log_dir=log_dir)
        ffmpeg.start()

        update_status(
            status_path,
            camera_id=camera_id,
            status="running",
            video_id=video_id,
            stream_id=stream_id,
            frame_count=0,
        )

        for jpeg_bytes in reader.frames():
            if not running:
                break

            frame_count += 1
            last_frame_at = utc_now()

            # RequestStream đã xin Fps=2, nên không cần throttle thêm ở đây.
            # Nếu server trả cao hơn 2fps, có thể bật throttle bằng env WORKER_FORCE_THROTTLE=true.
            force_throttle = getattr(settings, "worker_force_throttle", False)
            if force_throttle:
                time.sleep(max(0.0, settings.worker_frame_interval_seconds))

            atomic_write_bytes(thumbnail_path, jpeg_bytes)
            atomic_write_bytes(latest_path, jpeg_bytes)
            ffmpeg.write_jpeg(jpeg_bytes)

            if frame_count == 1 or frame_count % 10 == 0:
                update_status(
                    status_path,
                    camera_id=camera_id,
                    status="running",
                    video_id=video_id,
                    stream_id=stream_id,
                    frame_count=frame_count,
                    last_frame_at=last_frame_at,
                )

    finally:
        update_status(
            status_path,
            camera_id=camera_id,
            status="stopping",
            video_id=video_id,
            stream_id=stream_id,
            frame_count=frame_count,
        )

        # Thứ tự quan trọng: CloseStream TRƯỚC khi đóng socket. Đóng socket trước
        # thì Mobile Server vẫn đang push frame vào socket đã chết và log
        # "HttpListenerException: nonexistent network connection".
        if client and video_id:
            client.close_stream(video_id)

        if response is not None:
            try:
                response.close()
            except Exception:
                pass

        _active_response = None

        if ffmpeg:
            ffmpeg.stop()

        if client:
            # close() gửi LogOut trước khi đóng HTTP session, nên Milestone nhả
            # session ngay thay vì để nó chờ timeout.
            client.close()

        update_status(
            status_path,
            camera_id=camera_id,
            status="stopped",
            video_id=video_id,
            stream_id=stream_id,
            frame_count=frame_count,
        )

    return frame_count


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--hls-dir", required=True)
    parser.add_argument("--thumbnail-path", required=True)
    parser.add_argument("--latest-path", required=False)
    parser.add_argument("--status-path", required=False)
    parser.add_argument("--log-dir", required=False)

    args = parser.parse_args()

    # Worker chạy như subprocess, stdout/stderr được manager ghi vào
    # <hls_dir>/logs/worker.err.log. Không cấu hình ở đây thì chỉ WARNING trở lên
    # được in ra, và không có timestamp để đối chiếu với log Mobile Server.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    # Trên Windows, Popen.terminate() là TerminateProcess: không handler nào chạy,
    # nên khối finally ở trên bị bỏ qua và session Milestone rò lại. Manager vì vậy
    # gửi CTRL_BREAK_EVENT, tới process này dưới dạng SIGBREAK.
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_stop)

    settings = get_settings()

    camera_id = args.camera_id
    hls_dir = Path(args.hls_dir)
    thumbnail_path = Path(args.thumbnail_path)
    latest_path = Path(args.latest_path) if args.latest_path else hls_dir / "latest.jpg"
    status_path = Path(args.status_path) if args.status_path else hls_dir / "worker_status.json"
    log_dir = Path(args.log_dir) if args.log_dir else hls_dir / "logs"

    hls_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    base_delay = settings.milestone_reconnect_delay_seconds
    max_delay = settings.milestone_reconnect_max_delay_seconds
    empty_sessions = 0

    while running:
        try:
            frame_count = run_once(
                camera_id=camera_id,
                hls_dir=hls_dir,
                thumbnail_path=thumbnail_path,
                latest_path=latest_path,
                status_path=status_path,
                log_dir=log_dir,
            )
        except Exception as exc:
            # worker_status.json chỉ giữ được repr(exc); traceback đầy đủ mới cho biết
            # phiên stream chết ở đâu (đọc HTTP, ghi vào ffmpeg, hay RequestStream).
            logger.exception("camera %s: phiên stream kết thúc do lỗi", camera_id)
            update_status(
                status_path,
                camera_id=camera_id,
                status="error",
                message=repr(exc),
            )
            frame_count = 0

        if not running:
            break

        # Phiên có frame => reconnect nhanh như cũ. Phiên không ra frame nào
        # (camera chết, RequestStream fail...) => giãn dần, tránh login liên tục vô ích.
        if frame_count > 0:
            empty_sessions = 0
            delay = base_delay
        else:
            empty_sessions += 1
            delay = min(base_delay * (2 ** (empty_sessions - 1)), max_delay)

        logger.info(
            "camera %s: phiên kết thúc với %d frame, reconnect sau %.0fs",
            camera_id,
            frame_count,
            delay,
        )
        time.sleep(delay)


if __name__ == "__main__":
    main()

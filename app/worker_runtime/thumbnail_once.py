import argparse
from pathlib import Path

from app.core.config import get_settings
from app.milestone.jpeg_stream_reader import MilestoneJPEGStreamReader
from app.milestone.mobile_client import MilestoneMobileClient
from app.utils.paths import get_thumbnail_path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def capture_first_frame(
    client: MilestoneMobileClient,
    camera_id: str,
    thumbnail_path: Path,
) -> bool:
    """
    Lấy 1 frame cho 1 camera trên session đã login sẵn.
    RequestStream + CloseStream dùng lại session chung, không login lại.
    """
    response = None
    video_id = None

    try:
        stream_result = client.request_stream(camera_id=camera_id, fps=1, width=854, height=480)
        video_id = stream_result.video_id

        response = client.open_video_stream(video_id)

        for jpeg_bytes in MilestoneJPEGStreamReader(response).frames():
            atomic_write_bytes(thumbnail_path, jpeg_bytes)
            return True

        return False

    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

        if video_id:
            client.close_stream(video_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--camera-id",
        action="append",
        required=True,
        help="Có thể truyền nhiều lần; tất cả camera dùng chung 1 lần login.",
    )
    parser.add_argument(
        "--thumbnail-path",
        help="Chỉ dùng khi truyền đúng 1 --camera-id; mặc định lấy theo THUMBNAIL_ROOT.",
    )
    args = parser.parse_args()

    camera_ids: list[str] = args.camera_id

    if args.thumbnail_path and len(camera_ids) != 1:
        parser.error("--thumbnail-path chỉ dùng được khi có đúng 1 --camera-id")

    settings = get_settings()

    # Read timeout ngắn hơn stream worker: camera chết thì bỏ qua nhanh, không giữ session lâu.
    client = MilestoneMobileClient(
        stream_timeout_seconds=settings.thumbnail_capture_timeout_seconds
    )

    try:
        client.connect_and_login()

        for camera_id in camera_ids:
            thumbnail_path = (
                Path(args.thumbnail_path)
                if args.thumbnail_path
                else get_thumbnail_path(camera_id)
            )

            try:
                capture_first_frame(client, camera_id, thumbnail_path)
            except Exception as exc:
                # Một camera lỗi không được làm hỏng các camera còn lại trong batch.
                print(f"thumbnail_once: camera {camera_id} thất bại: {exc!r}", flush=True)

    finally:
        # close() đã LogOut, session được nhả ngay thay vì chờ server timeout.
        client.close()


if __name__ == "__main__":
    main()

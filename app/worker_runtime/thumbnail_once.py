import argparse
from pathlib import Path

from app.milestone.jpeg_stream_reader import MilestoneJPEGStreamReader
from app.milestone.mobile_client import MilestoneMobileClient


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--thumbnail-path", required=True)
    args = parser.parse_args()

    camera_id = args.camera_id
    thumbnail_path = Path(args.thumbnail_path)

    client = MilestoneMobileClient()
    response = None
    video_id = None

    try:
        client.connect_and_login()
        stream_result = client.request_stream(camera_id=camera_id, fps=1, width=854, height=480)
        video_id = stream_result.video_id

        response = client.open_video_stream(video_id)
        reader = MilestoneJPEGStreamReader(response)

        for jpeg_bytes in reader.frames():
            atomic_write_bytes(thumbnail_path, jpeg_bytes)
            break

    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

        if video_id:
            client.close_stream(video_id)

        client.close()


if __name__ == "__main__":
    main()

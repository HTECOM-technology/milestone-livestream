from collections.abc import Iterator
import requests

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class MilestoneJPEGStreamReader:
    """
    Milestone /XProtectMobile/Video/{VideoId} ở môi trường đã probe trả Content-Type=image/jpeg
    nhưng body là stream binary liên tục, bên trong có nhiều JPEG frame.
    Reader này không phụ thuộc header frame riêng của Milestone; nó tách frame bằng SOI/EOI.
    """

    def __init__(self, response: requests.Response, chunk_size: int = 8192, max_buffer_bytes: int = 8 * 1024 * 1024) -> None:
        self.response = response
        self.chunk_size = chunk_size
        self.max_buffer_bytes = max_buffer_bytes

    def frames(self) -> Iterator[bytes]:
        buffer = bytearray()

        for chunk in self.response.iter_content(chunk_size=self.chunk_size):
            if not chunk:
                continue

            buffer.extend(chunk)

            while True:
                start = buffer.find(JPEG_SOI)
                if start < 0:
                    # Giữ buffer nhỏ để tránh phình RAM nếu stream có header nhị phân dài.
                    if len(buffer) > self.max_buffer_bytes:
                        del buffer[:-2]
                    break

                end = buffer.find(JPEG_EOI, start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    if len(buffer) > self.max_buffer_bytes:
                        del buffer[:-2]
                    break

                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]

                if frame.startswith(JPEG_SOI) and frame.endswith(JPEG_EOI):
                    yield frame

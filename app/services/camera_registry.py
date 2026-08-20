from typing import List, Optional

from app.services.camera_cache import camera_cache
from app.milestone.mobile_client import MilestoneMobileClient
from app.services.camera_types import CameraDefinition


def fix_mojibake(value: str) -> str:
    """
    Một số camera có tên đã bị mojibake sẵn trong Milestone: bytes UTF-8 bị lưu lại
    sau khi decode bằng latin-1, nên "Đồng Nai" thành "Ä\x90á»\x93ng Nai".

    Không dùng heuristic theo ký tự cụ thể. Bản trước lọc bằng `"Ã" not in value`,
    nhưng "Ã" chỉ xuất hiện khi byte đầu là C3 (dải U+00C0-U+00FF); phần lớn ký tự
    tiếng Việt lại nằm ngoài dải đó — ơ U+01A1 -> C6 A1 ("Æ¡"), Đ U+0110 -> C4 90
    ("Ä\x90"), ồ U+1ED3 -> E1 BB 93 ("á»\x93") — nên đúng những tên cần sửa lại bị
    loại. Hai phép chuyển đổi dưới đây tự làm bộ lọc chặt hơn mọi heuristic:

    - encode("latin-1") raise ngay nếu có codepoint > 0xFF, tức tên đã đúng (tên
      tiếng Việt đúng luôn chứa ký tự như "ơ", "ạ", "Đ").
    - decode("utf-8") raise nếu chuỗi bytes không phải UTF-8 hợp lệ, nên tên có ký
      tự latin-1 hợp lệ thật (ví dụ "Café") không bị đổi.

    Tên toàn ASCII đi qua cả hai phép mà không thay đổi, nên trả về nguyên trạng.
    """
    if not value:
        return value

    try:
        decoded = value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value

    return decoded if decoded != value else value


class CameraRegistry:
    def list_cameras(self, refresh: bool = False) -> list[CameraDefinition]:
        if not refresh:
            cached = camera_cache.get()
            if cached is not None:
                return cached

        try:
            cameras = self._fetch_from_milestone()
            camera_cache.set(cameras)
            return cameras
        except Exception:
            cached = camera_cache.get()
            if cached is not None:
                return cached
            raise

    def get_camera(self, camera_id: str) -> Optional[CameraDefinition]:
        for camera in self.list_cameras(refresh=False):
            if camera.camera_id == camera_id:
                return camera

        # fallback refresh một lần nếu cache chưa có camera mới
        for camera in self.list_cameras(refresh=True):
            if camera.camera_id == camera_id:
                return camera

        return None

    def _fetch_from_milestone(self) -> list[CameraDefinition]:
        client = MilestoneMobileClient()

        try:
            client.connect_and_login()
            xml_text = client.get_all_views_and_cameras()
            return self._parse_cameras_from_xml(xml_text)
        finally:
            # close() gửi LogOut trước khi đóng HTTP session.
            client.close()

    def _parse_cameras_from_xml(self, xml_text: str) -> list[CameraDefinition]:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)

        cameras: list[CameraDefinition] = []
        seen_ids: set[str] = set()

        for item in root.iter():
            if item.attrib.get("Type") != "Camera":
                continue

            camera_id = item.attrib.get("Id")
            name = item.attrib.get("Name", "")

            if not camera_id or camera_id in seen_ids:
                continue

            seen_ids.add(camera_id)

            cameras.append(
                CameraDefinition(
                    camera_id=camera_id,
                    name=fix_mojibake(name),
                    description=None,
                )
            )

        return cameras


camera_registry = CameraRegistry()

from typing import List, Optional

from app.services.camera_cache import camera_cache
from app.milestone.mobile_client import MilestoneMobileClient
from app.services.camera_types import CameraDefinition


def fix_mojibake(value: str) -> str:
    if not value:
        return value

    if "Ã" not in value and "á" in value:
        return value

    try:
        return value.encode("latin1").decode("utf-8")
    except UnicodeError:
        return value


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

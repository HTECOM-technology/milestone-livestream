from dataclasses import dataclass
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class CameraDefinition:
    camera_id: str
    name: str
    description: str | None = None
    live: bool = True
    ptz: bool = False


def parse_cameras_from_views_xml(xml_text: str) -> list[CameraDefinition]:
    root = ET.fromstring(xml_text)
    cameras: dict[str, CameraDefinition] = {}

    for elem in root.iter():
        if elem.tag.split('}')[-1] != 'Item':
            continue
        if elem.attrib.get('Type') != 'Camera':
            continue

        camera_id = elem.attrib.get('Id')
        name = elem.attrib.get('Name') or camera_id or 'Unknown Camera'
        if not camera_id:
            continue

        properties = None
        for child in list(elem):
            if child.tag.split('}')[-1] == 'Properties':
                properties = child
                break

        live = True
        ptz = False
        if properties is not None:
            live = properties.attrib.get('Live', 'Yes') == 'Yes'
            ptz = properties.attrib.get('PTZ', 'No') == 'Yes'

        # XML có thể lặp camera trong All Cameras và Public Views; dedupe theo Id.
        cameras[camera_id] = CameraDefinition(
            camera_id=camera_id,
            name=name,
            description=None,
            live=live,
            ptz=ptz,
        )

    return sorted(cameras.values(), key=lambda c: c.name.lower())

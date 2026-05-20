from dataclasses import dataclass


@dataclass(frozen=True)
class CameraDefinition:
    camera_id: str
    name: str
    description: str | None = None

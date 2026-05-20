from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CameraItem(BaseModel):
    camera_id: str
    name: str
    description: Optional[str] = None
    is_active: bool = False
    viewer_count: int = 0
    thumbnail_url: str
    latest_url: Optional[str] = None
    hls_url: Optional[str] = None
    last_frame_at: Optional[datetime] = None


class CameraListResponse(BaseModel):
    items: list[CameraItem]


class WatchStartRequest(BaseModel):
    session_id: Optional[str] = Field(default=None)


class WatchStartResponse(BaseModel):
    camera_id: str
    session_id: str
    viewer_count: int
    hls_url: str
    latest_url: str
    status: str


class HeartbeatRequest(BaseModel):
    session_id: str


class HeartbeatResponse(BaseModel):
    camera_id: str
    session_id: str
    viewer_count: int
    status: str


class WatchStopRequest(BaseModel):
    session_id: str


class WatchStopResponse(BaseModel):
    camera_id: str
    session_id: str
    viewer_count: int
    status: str


class CameraStatusResponse(BaseModel):
    camera_id: str
    is_active: bool
    viewer_count: int
    hls_url: Optional[str] = None
    latest_url: Optional[str] = None
    thumbnail_url: str
    last_frame_at: Optional[datetime] = None
    worker_pid: Optional[int] = None
    worker_status: str
    worker_status_detail: Optional[dict] = None

from fastapi import APIRouter, HTTPException, Query

from app.schemas.camera import (
    CameraItem,
    CameraListResponse,
    CameraStatusResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    WatchStartRequest,
    WatchStartResponse,
    WatchStopRequest,
    WatchStopResponse,
)
from app.services.camera_registry import camera_registry
from app.services.viewer_store import viewer_store
from app.utils.paths import build_hls_url, build_latest_url, build_thumbnail_url
from app.worker_manager.manager import camera_worker_manager

router = APIRouter(prefix='/api/cameras', tags=['cameras'])


@router.get("", response_model=CameraListResponse)
def list_cameras(refresh: bool = False) -> CameraListResponse:
    items: list[CameraItem] = []

    for camera in camera_registry.list_cameras(refresh=refresh):
        worker = camera_worker_manager.get_worker(camera.camera_id)
        is_active = bool(worker and worker.is_running())
        viewer_count = viewer_store.count_viewers(camera.camera_id)

        items.append(
            CameraItem(
                camera_id=camera.camera_id,
                name=camera.name,
                description=camera.description,
                is_active=is_active,
                viewer_count=viewer_count,
                thumbnail_url=build_thumbnail_url(camera.camera_id),
                hls_url=build_hls_url(camera.camera_id) if is_active else None,
                last_frame_at=getattr(worker, "last_frame_at", None) if worker else None,
            )
        )

    return CameraListResponse(items=items)


@router.post('/{camera_id}/watch/start', response_model=WatchStartResponse)
def watch_start(camera_id: str, payload: WatchStartRequest) -> WatchStartResponse:
    camera = camera_registry.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail='Camera not found')

    worker = camera_worker_manager.start_worker(camera_id)
    session = viewer_store.start_session(camera_id, payload.session_id)

    return WatchStartResponse(
        camera_id=camera_id,
        session_id=session.session_id,
        viewer_count=viewer_store.count_viewers(camera_id),
        hls_url=build_hls_url(camera_id),
        latest_url=build_latest_url(camera_id),
        status=worker.status,
    )


@router.post('/{camera_id}/watch/heartbeat', response_model=HeartbeatResponse)
def watch_heartbeat(camera_id: str, payload: HeartbeatRequest) -> HeartbeatResponse:
    camera = camera_registry.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail='Camera not found')

    session = viewer_store.heartbeat(camera_id, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail='Viewer session not found')

    worker = camera_worker_manager.get_worker(camera_id)
    if not worker or not worker.is_running():
        worker = camera_worker_manager.start_worker(camera_id)

    return HeartbeatResponse(
        camera_id=camera_id,
        session_id=session.session_id,
        viewer_count=viewer_store.count_viewers(camera_id),
        status=worker.status,
    )


@router.post('/{camera_id}/watch/stop', response_model=WatchStopResponse)
def watch_stop(camera_id: str, payload: WatchStopRequest) -> WatchStopResponse:
    camera = camera_registry.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail='Camera not found')

    viewer_store.stop_session(camera_id, payload.session_id)
    viewer_count = viewer_store.count_viewers(camera_id)
    if viewer_count <= 0:
        camera_worker_manager.stop_worker(camera_id)
        status = 'stopped'
    else:
        status = 'running'

    return WatchStopResponse(
        camera_id=camera_id,
        session_id=payload.session_id,
        viewer_count=viewer_count,
        status=status,
    )


@router.get('/{camera_id}/status', response_model=CameraStatusResponse)
def camera_status(camera_id: str) -> CameraStatusResponse:
    camera = camera_registry.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail='Camera not found')

    worker = camera_worker_manager.get_worker(camera_id)
    is_active = bool(worker and worker.is_running())
    status_detail = camera_worker_manager.read_worker_status(camera_id)

    return CameraStatusResponse(
        camera_id=camera_id,
        is_active=is_active,
        viewer_count=viewer_store.count_viewers(camera_id),
        hls_url=build_hls_url(camera_id) if is_active else None,
        latest_url=build_latest_url(camera_id) if is_active else None,
        thumbnail_url=build_thumbnail_url(camera_id),
        last_frame_at=status_detail.get('last_frame_at') if status_detail else None,
        worker_pid=worker.pid if worker else None,
        worker_status=worker.status if worker else 'stopped',
        worker_status_detail=status_detail,
    )

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.services.camera_registry import camera_registry
from app.worker_manager.manager import camera_worker_manager

scheduler = BackgroundScheduler()


def refresh_inactive_camera_thumbnails() -> None:
    try:
        cameras = camera_registry.list_cameras(force_refresh=False)
    except Exception:
        return

    for camera in cameras:
        if camera_worker_manager.is_active(camera.camera_id):
            continue
        camera_worker_manager.refresh_thumbnail_once(camera.camera_id)


def start_thumbnail_refresh_job() -> None:
    settings = get_settings()
    scheduler.add_job(
        refresh_inactive_camera_thumbnails,
        'interval',
        minutes=settings.thumbnail_refresh_interval_minutes,
        id='refresh_inactive_camera_thumbnails',
        replace_existing=True,
        max_instances=1,
    )
    if not scheduler.running:
        scheduler.start()


def shutdown_thumbnail_refresh_job() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)

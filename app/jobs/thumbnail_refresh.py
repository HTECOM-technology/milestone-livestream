import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.services.camera_registry import camera_registry
from app.worker_manager.manager import camera_worker_manager

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def refresh_inactive_camera_thumbnails() -> None:
    try:
        cameras = camera_registry.list_cameras(refresh=False)
    except Exception:
        # Không nuốt lỗi: nếu lấy danh sách camera fail thì phải thấy được trong log.
        logger.exception("refresh_inactive_camera_thumbnails: không lấy được danh sách camera")
        return

    inactive_camera_ids = [
        camera.camera_id
        for camera in cameras
        if not camera_worker_manager.is_active(camera.camera_id)
    ]

    if not inactive_camera_ids:
        return

    try:
        # Một subprocess cho cả danh sách => một lần login vào Milestone mỗi vòng job.
        camera_worker_manager.refresh_thumbnails_once(inactive_camera_ids)
    except Exception:
        logger.exception(
            "refresh_inactive_camera_thumbnails: không spawn được batch refresh cho %d camera",
            len(inactive_camera_ids),
        )


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

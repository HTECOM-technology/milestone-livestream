import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cameras import router as cameras_router
from app.core.config import get_settings
from app.jobs.thumbnail_refresh import start_thumbnail_refresh_job, shutdown_thumbnail_refresh_job
from app.services.viewer_store import viewer_store
from app.worker_manager.manager import camera_worker_manager


async def cleanup_stale_viewers_loop() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.viewer_cleanup_interval_seconds)
        removed = viewer_store.remove_stale_sessions(settings.viewer_heartbeat_ttl_seconds)
        if not removed:
            continue
        for camera_id in sorted({s.camera_id for s in removed}):
            if viewer_store.count_viewers(camera_id) <= 0:
                camera_worker_manager.stop_worker(camera_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(cleanup_stale_viewers_loop())
    start_thumbnail_refresh_job()
    try:
        yield
    finally:
        cleanup_task.cancel()
        shutdown_thumbnail_refresh_job()
        for camera_id in camera_worker_manager.list_active_camera_ids():
            camera_worker_manager.stop_worker(camera_id)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.include_router(cameras_router)

    @app.get('/health')
    def health_check():
        return {'status': 'ok'}

    return app


app = create_app()

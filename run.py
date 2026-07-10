import uvicorn
from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        'app.main:app',
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )


if __name__ == '__main__':
    main()

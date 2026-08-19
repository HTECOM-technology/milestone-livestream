import logging

import uvicorn
from app.core.config import get_settings


def main() -> None:
    # Uvicorn chỉ gắn handler cho logger của chính nó; không có dòng này thì log của
    # app.* rơi vào lastResort handler và mất hết INFO (ví dụ tên lệnh logout mà
    # Mobile Server chấp nhận).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = get_settings()
    uvicorn.run(
        'app.main:app',
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )


if __name__ == '__main__':
    main()

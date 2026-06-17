import uvicorn
from fastapi import FastAPI

from disappearing_tasks.api.router import api_router
from disappearing_tasks.config import get_settings
from disappearing_tasks.lifespan import lifespan
from disappearing_tasks.logging import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description="Demo: SQLite checkpoints + graceful shutdown + resume",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "disappearing_tasks.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        # Ważne: pozwala lifespan dokończyć shutdown przed exit
        timeout_graceful_shutdown=int(settings.shutdown_timeout_seconds),
    )


if __name__ == "__main__":
    run()

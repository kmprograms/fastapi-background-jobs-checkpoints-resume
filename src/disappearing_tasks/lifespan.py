from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI

from disappearing_tasks.config import get_settings
from disappearing_tasks.infrastructure.sqlite_task_store import SqliteTaskStore
from disappearing_tasks.services.task_manager import BackgroundTaskManager, VideoProcessorService

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    store = SqliteTaskStore(settings.database_path)
    await store.connect()

    task_manager = BackgroundTaskManager(settings)
    processor = VideoProcessorService(
        store,
        task_manager,
        settings,
        worker_id=settings.worker_id,
    )

    app.state.settings = settings
    app.state.store = store
    app.state.task_manager = task_manager
    app.state.processor = processor

    logger.info(
        "server_starting",
        worker_id=settings.worker_id,
        video_steps=settings.video_steps,
        step_delay=settings.video_step_delay_seconds,
        database=str(settings.database_path),
    )

    if settings.auto_resume_on_startup:
        resumed = await processor.resume_all_interrupted()
        if resumed:
            logger.warning("orphaned_tasks_resumed_on_startup", count=resumed)

    yield

    await task_manager.shutdown()
    await store.close()
    logger.info("server_stopped")

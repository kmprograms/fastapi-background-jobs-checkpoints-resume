import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import structlog

from disappearing_tasks.config import Settings
from disappearing_tasks.domain.models import TaskStatus, VideoTask
from disappearing_tasks.domain.ports import TaskStore

logger = structlog.get_logger(__name__)

type TaskCoroutineFactory = Callable[[], Awaitable[None]]


class BackgroundTaskManager:
    """
    Rejestr tasków w tle + kontrolowany shutdown.

    To jest warstwa, która daje procesowi czas na dokończenie pracy
    przy planowanym SIGTERM. Nie zastępuje checkpointów w DB.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False
        self._shutdown_event = asyncio.Event()

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def spawn(self, coro_factory: TaskCoroutineFactory, *, name: str) -> asyncio.Task[Any]:
        if self._shutting_down:
            raise RuntimeError("Serwer w trakcie graceful shutdown — nowe taski są odrzucane")

        task = asyncio.create_task(coro_factory(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        logger.info("task_spawned", task_name=name, active_tasks=self.active_count)
        return task

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            logger.warning("task_cancelled", task_name=task.get_name())
        elif exc := task.exception():
            logger.error("task_failed", task_name=task.get_name(), error=str(exc))
        else:
            logger.info("task_finished", task_name=task.get_name(), active_tasks=self.active_count)

        if self._shutting_down and not self._tasks:
            self._shutdown_event.set()

    async def shutdown(self) -> None:
        if self._shutting_down:
            return

        self._shutting_down = True
        pending = len(self._tasks)

        if pending == 0:
            logger.info("shutdown_complete", reason="brak aktywnych tasków")
            return

        logger.warning(
            "graceful_shutdown_started",
            pending_tasks=pending,
            timeout_seconds=self._settings.shutdown_timeout_seconds,
        )

        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=self._settings.shutdown_timeout_seconds,
            )
            logger.info("graceful_shutdown_complete", remaining_tasks=0)
        except TimeoutError:
            still_running = len(self._tasks)
            logger.error(
                "graceful_shutdown_timeout",
                remaining_tasks=still_running,
                action="anulowanie pozostałych tasków",
            )
            await self._cancel_remaining()

    async def _cancel_remaining(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


class VideoProcessorService:
    """
    Długie przetwarzanie z checkpointami.
    1. Checkpoint do SQLite po każdym kroku (przeżywa SIGKILL / restart)
    2. Graceful shutdown przez BackgroundTaskManager (przeżywa SIGTERM)
    3. Resume od ostatniego checkpointu po starcie procesu
    """

    def __init__(
        self,
        store: TaskStore,
        task_manager: BackgroundTaskManager,
        settings: Settings,
        *,
        worker_id: str,
    ) -> None:
        self._store = store
        self._task_manager = task_manager
        self._settings = settings
        self._worker_id = worker_id

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def enqueue(self, filename: str) -> VideoTask:
        task = VideoTask(
            filename=filename,
            total_steps=self._settings.video_steps,
            worker_id=self._worker_id,
        )
        await self._store.create(task)

        self._task_manager.spawn(
            lambda: self._process(task.id, resume=False),
            name=f"video-{task.id}",
        )
        return task

    async def resume(self, task_id: UUID) -> VideoTask:
        task = await self._store.claim_for_resume(task_id, self._worker_id)
        if task is None:
            raise ValueError(f"Task {task_id} nie może być wznowiony")

        self._task_manager.spawn(
            lambda: self._process(task_id, resume=True),
            name=f"resume-{task_id}",
        )
        return task

    async def resume_all_interrupted(self) -> int:
        resumable = await self._store.find_resumable()
        resumed = 0

        for task in resumable:
            try:
                await self.resume(task.id)
                resumed += 1
            except ValueError:
                continue

        if resumed:
            logger.warning(
                "auto_resume_started",
                count=resumed,
                worker_id=self._worker_id,
            )
        return resumed

    async def _process(self, task_id: UUID, *, resume: bool) -> None:
        task = await self._store.get(task_id)
        if task is None:
            return

        start_step = task.current_step + 1
        log = logger.bind(
            task_id=str(task_id),
            filename=task.filename,
            worker_id=self._worker_id,
            resume=resume,
            start_step=start_step,
        )

        if resume:
            log.warning(
                "video_processing_resumed",
                from_step=start_step,
                checkpoint_step=task.current_step,
                total_steps=task.total_steps,
            )
        else:
            log.info("video_processing_started", total_steps=task.total_steps)

        try:
            for step in range(start_step, task.total_steps + 1):
                if self._task_manager.is_shutting_down:
                    log.info("finishing_step_before_shutdown", step=step)

                await self._simulate_step(step, task.total_steps)
                # CHECKPOINT — stan przeżywa śmierć procesu
                await self._store.mark_step(task_id, step)
                log.info(
                    "checkpoint_saved",
                    step=step,
                    percent=round(step / task.total_steps * 100),
                )

            await self._store.mark_completed(task_id)
            log.info("video_processing_completed")
        except asyncio.CancelledError:
            await self._store.mark_interrupted(task_id)
            refreshed = await self._store.get(task_id)
            log.warning(
                "video_processing_interrupted",
                last_checkpoint=refreshed.current_step if refreshed else None,
                resumable=True,
            )
            raise
        except Exception as exc:
            await self._store.mark_failed(task_id, str(exc))
            log.exception("video_processing_failed")
            raise

    async def _simulate_step(self, step: int, total: int) -> None:
        delay = self._settings.video_step_delay_seconds
        jitter = 0.1 * (step % 3)
        await asyncio.sleep(delay + jitter)
        _ = total

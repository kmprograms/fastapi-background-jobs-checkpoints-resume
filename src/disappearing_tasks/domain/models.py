from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class VideoTask(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    filename: str
    status: TaskStatus = TaskStatus.PENDING
    current_step: int = 0
    total_steps: int = 10
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    worker_id: str | None = None

    @property
    def progress_percent(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return round((self.current_step / self.total_steps) * 100, 1)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)


class CreateVideoTaskRequest(BaseModel):
    filename: str = Field(
        examples=["wedding_clip_4k.mp4"],
        min_length=1,
        max_length=255,
    )


class VideoTaskResponse(BaseModel):
    id: UUID
    filename: str
    status: TaskStatus
    current_step: int
    total_steps: int
    progress_percent: float
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    worker_id: str | None = None

    @classmethod
    def from_task(cls, task: VideoTask) -> VideoTaskResponse:
        return cls(
            id=task.id,
            filename=task.filename,
            status=task.status,
            current_step=task.current_step,
            total_steps=task.total_steps,
            progress_percent=task.progress_percent,
            created_at=task.created_at,
            updated_at=task.updated_at,
            error=task.error,
            worker_id=task.worker_id,
        )


class HealthResponse(BaseModel):
    status: str
    active_background_tasks: int
    graceful_shutdown: bool
    durable_store: bool
    worker_id: str | None = None
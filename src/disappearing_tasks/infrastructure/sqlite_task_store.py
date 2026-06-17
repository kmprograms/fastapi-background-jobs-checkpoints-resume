import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite

from disappearing_tasks.domain.models import TaskStatus, VideoTask

_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_tasks (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step INTEGER NOT NULL,
    total_steps INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    worker_id TEXT
);
"""


class SqliteTaskStore:
    """Trwały magazyn checkpointów — przeżywa restart procesu."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._database_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteTaskStore nie jest połączony — wywołaj connect()")
        return self._conn

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> VideoTask:
        return VideoTask(
            id=UUID(row["id"]),
            filename=row["filename"],
            status=TaskStatus(row["status"]),
            current_step=row["current_step"],
            total_steps=row["total_steps"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            error=row["error"],
            worker_id=row["worker_id"],
        )

    @staticmethod
    def _task_to_row(task: VideoTask) -> dict[str, object]:
        return {
            "id": str(task.id),
            "filename": task.filename,
            "status": task.status.value,
            "current_step": task.current_step,
            "total_steps": task.total_steps,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "error": task.error,
            "worker_id": task.worker_id,
        }

    async def _fetch_one(self, conn: aiosqlite.Connection, task_id: UUID) -> VideoTask | None:
        cursor = await conn.execute(
            "SELECT * FROM video_tasks WHERE id = ?",
            (str(task_id),),
        )
        row = await cursor.fetchone()
        return self._row_to_task(row) if row else None

    async def _upsert(self, conn: aiosqlite.Connection, task: VideoTask) -> VideoTask:
        row = self._task_to_row(task)
        await conn.execute(
            """
            INSERT INTO video_tasks (
                id, filename, status, current_step, total_steps,
                created_at, updated_at, error, worker_id
            ) VALUES (
                :id, :filename, :status, :current_step, :total_steps,
                :created_at, :updated_at, :error, :worker_id
            )
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                current_step = excluded.current_step,
                total_steps = excluded.total_steps,
                updated_at = excluded.updated_at,
                error = excluded.error,
                worker_id = excluded.worker_id
            """,
            row,
        )
        await conn.commit()
        return task

    async def create(self, task: VideoTask) -> VideoTask:
        async with self._lock:
            return await self._upsert(self._require_conn(), task)

    async def get(self, task_id: UUID) -> VideoTask | None:
        async with self._lock:
            return await self._fetch_one(self._require_conn(), task_id)

    async def update(self, task: VideoTask) -> VideoTask:
        async with self._lock:
            task.touch()
            return await self._upsert(self._require_conn(), task)

    async def mark_step(self, task_id: UUID, step: int) -> VideoTask | None:
        async with self._lock:
            conn = self._require_conn()
            task = await self._fetch_one(conn, task_id)
            if task is None:
                return None
            task.current_step = step
            task.status = TaskStatus.RUNNING
            task.touch()
            return await self._upsert(conn, task)

    async def mark_completed(self, task_id: UUID) -> VideoTask | None:
        async with self._lock:
            conn = self._require_conn()
            task = await self._fetch_one(conn, task_id)
            if task is None:
                return None
            task.current_step = task.total_steps
            task.status = TaskStatus.COMPLETED
            task.error = None
            task.touch()
            return await self._upsert(conn, task)

    async def mark_failed(self, task_id: UUID, error: str) -> VideoTask | None:
        async with self._lock:
            conn = self._require_conn()
            task = await self._fetch_one(conn, task_id)
            if task is None:
                return None
            task.status = TaskStatus.FAILED
            task.error = error
            task.touch()
            return await self._upsert(conn, task)

    async def mark_interrupted(self, task_id: UUID) -> VideoTask | None:
        async with self._lock:
            conn = self._require_conn()
            task = await self._fetch_one(conn, task_id)
            if task is None:
                return None
            task.status = TaskStatus.INTERRUPTED
            task.error = (
                "Proces ubity w trakcie pracy — checkpoint zachowany, możliwy resume"
            )
            task.touch()
            return await self._upsert(conn, task)

    async def list_all(self) -> list[VideoTask]:
        async with self._lock:
            conn = self._require_conn()
            cursor = await conn.execute("SELECT * FROM video_tasks ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    async def find_resumable(self) -> list[VideoTask]:
        async with self._lock:
            conn = self._require_conn()
            cursor = await conn.execute(
                """
                SELECT * FROM video_tasks
                WHERE status IN (?, ?)
                  AND current_step < total_steps
                ORDER BY updated_at ASC
                """,
                (TaskStatus.RUNNING.value, TaskStatus.INTERRUPTED.value),
            )
            rows = await cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    async def claim_for_resume(self, task_id: UUID, worker_id: str) -> VideoTask | None:
        async with self._lock:
            conn = self._require_conn()
            task = await self._fetch_one(conn, task_id)
            if task is None or task.status == TaskStatus.COMPLETED:
                return None
            task.status = TaskStatus.RUNNING
            task.worker_id = worker_id
            task.error = None
            task.touch()
            return await self._upsert(conn, task)
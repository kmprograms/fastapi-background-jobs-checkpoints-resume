from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from disappearing_tasks.domain.models import (
    CreateVideoTaskRequest,
    HealthResponse,
    VideoTaskResponse,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    task_manager = request.app.state.task_manager
    settings = request.app.state.settings

    return HealthResponse(
        status="ok",
        active_background_tasks=task_manager.active_count,
        graceful_shutdown=True,
        durable_store=True,
        worker_id=settings.worker_id,
    )


@router.post(
    "/tasks/video",
    response_model=VideoTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video_task(
    request: Request,
    body: CreateVideoTaskRequest,
) -> VideoTaskResponse:
    processor = request.app.state.processor
    task_manager = request.app.state.task_manager

    if task_manager.is_shutting_down:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serwer w trakcie graceful shutdown — spróbuj ponownie za chwilę",
        )

    task = await processor.enqueue(body.filename)
    return VideoTaskResponse.from_task(task)


@router.get("/tasks/{task_id}", response_model=VideoTaskResponse)
async def get_task(request: Request, task_id: UUID) -> VideoTaskResponse:
    store = request.app.state.store
    task = await store.get(task_id)

    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task nie znaleziony")

    return VideoTaskResponse.from_task(task)


@router.get("/tasks", response_model=list[VideoTaskResponse])
async def list_tasks(request: Request) -> list[VideoTaskResponse]:
    store = request.app.state.store
    tasks = await store.list_all()
    return [VideoTaskResponse.from_task(t) for t in tasks]


@router.post(
    "/tasks/{task_id}/resume",
    response_model=VideoTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_task(request: Request, task_id: UUID) -> VideoTaskResponse:
    processor = request.app.state.processor
    task_manager = request.app.state.task_manager

    if task_manager.is_shutting_down:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Shutdown w toku")

    try:
        task = await processor.resume(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return VideoTaskResponse.from_task(task)

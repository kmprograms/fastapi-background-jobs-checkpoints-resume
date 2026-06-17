from fastapi import APIRouter

from disappearing_tasks.api.v1 import tasks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(tasks.router, tags=["tasks"])

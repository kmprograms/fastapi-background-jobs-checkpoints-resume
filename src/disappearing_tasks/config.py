from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FastAPI Background Jobs — Checkpoints & Resume"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"

    # Symulacja DŁUGIEGO przetwarzania
    video_steps: int = 30
    video_step_delay_seconds: float = 2.0

    # Graceful shutdown — drain przy SIGTERM
    shutdown_timeout_seconds: float = 120.0

    # Trwały magazyn checkpointów
    database_path: Path = Path("data/tasks.db")
    auto_resume_on_startup: bool = True

    # Identyfikator instancji workera — zmienia się po restarcie
    worker_id: str = Field(default_factory=lambda: str(uuid4())[:8])


@lru_cache
def get_settings() -> Settings:
    return Settings()
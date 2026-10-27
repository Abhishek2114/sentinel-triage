"""Application configuration via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration – reads from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Postgres ──────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://sentinel:sentinel_secret@localhost:5432/sentinel"
    )

    # ── Redis ─────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── LLM / Embeddings ─────────────────────────────
    openai_api_key: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"

    # ── App ───────────────────────────────────────────
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    # create_all on startup is a dev convenience; Docker runs Alembic instead
    auto_create_tables: bool = True
    # /ingest may only read files under this directory
    ingest_root: str = "data"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()

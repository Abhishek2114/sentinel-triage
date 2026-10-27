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
    # Any OpenAI-compatible chat endpoint. With neither a key nor a base URL,
    # triage still runs and writes a rule-based report instead.
    openai_api_key: str = ""
    llm_base_url: str | None = None  # e.g. http://localhost:11434/v1 for Ollama
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    # A 384-d sentence-transformers model, or "hashing" for the offline embedder
    embedding_model: str = "all-MiniLM-L6-v2"
    runbooks_dir: str = "data/runbooks"

    # ── App ───────────────────────────────────────────
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    # create_all on startup is a dev convenience; Docker runs Alembic instead
    auto_create_tables: bool = True
    # /ingest may only read files under this directory
    ingest_root: str = "data"

    # ── Detection schedule ───────────────────────────
    detection_interval_seconds: int = 300
    # Each run refits on this much history, so it must cover many normal windows
    detection_lookback_minutes: int = 360
    # Only these detectors' anomalies become incidents. Isolation Forest raises
    # ~34 false alarms a day (README results), so it isn't triaged by default.
    triage_detectors: list[str] = ["error_burst"]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()

"""Application configuration via pydantic-settings."""

from functools import lru_cache
from typing import Literal

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
    # "openai" (any OpenAI-compatible server) or "anthropic" (Claude)
    llm_provider: Literal["openai", "anthropic"] = "openai"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    # Spend guard, shared by every API and worker process through Postgres.
    # A call that would cross either limit is not made; triage falls back to
    # the rule-based report instead.
    llm_max_calls_per_day: int = 200
    llm_max_tokens_per_day: int = 500_000
    llm_max_output_tokens: int = 4000
    # A 384-d sentence-transformers model, or "hashing" for the offline embedder
    embedding_model: str = "all-MiniLM-L6-v2"
    runbooks_dir: str = "data/runbooks"
    # auto | dense | bm25 | char | hybrid | hybrid_dense. "auto" = bm25, which
    # the retrieval ablation's dev splits selected (README "Retrieval")
    retrieval_mode: Literal["auto", "dense", "bm25", "char", "hybrid", "hybrid_dense"] = "auto"

    # ── App ───────────────────────────────────────────
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    # "production" refuses to start without API keys and hides /docs
    environment: Literal["development", "production"] = "development"
    # create_all on startup is a dev convenience; Docker runs Alembic instead
    auto_create_tables: bool = True
    # /ingest may only read files under this directory
    ingest_root: str = "data"

    # ── Access control ───────────────────────────────
    # Keys accepted in "Authorization: Bearer <key>" or "X-API-Key". Empty
    # means no auth (development only). Keys in the URL are always rejected.
    api_keys: list[str] = []
    # Let anyone read incidents, logs and the overview without a key
    public_read: bool = False
    # Read-only public demo: public reads, writes still need a key, and
    # anonymous visitors may run the synthetic demo scenario
    demo_mode: bool = False
    demo_cooldown_seconds: int = 60

    # ── Input limits ─────────────────────────────────
    max_body_bytes: int = 5 * 1024 * 1024
    # Longer log lines are cut to this many characters (and marked as cut)
    max_line_chars: int = 16_384
    # Timestamps further ahead than this are replaced by the receive time
    max_clock_skew_seconds: int = 300
    # A multi-line event (stack trace) stops absorbing lines after this many
    max_multiline_lines: int = 200

    # ── Detection schedule ───────────────────────────
    detection_interval_seconds: int = 300
    # Each run refits on this much history, so it must cover many normal windows
    detection_lookback_minutes: int = 360
    # Only these detectors' anomalies become incidents. Isolation Forest raises
    # ~34 false alarms a day (README results), so it isn't triaged by default.
    triage_detectors: list[str] = ["error_burst"]
    # Detectors the schedule runs. Isolation Forest costs ~0.2 s per service
    # and isn't triaged by default, so it runs only when asked for.
    scheduled_detectors: list[str] = ["error_burst"]
    # Detection cost is per service; past this many, the busiest are kept
    max_services_per_detection: int = 200
    # A triaged anomaly joins an open incident on the same service if it starts
    # within this many minutes of it, instead of opening a new incident
    incident_group_minutes: int = 15

    # ── Live alerts ──────────────────────────────────
    # API processes relay incident events from Redis to WebSocket clients
    realtime_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()

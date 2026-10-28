"""Application configuration via pydantic-settings."""

import re
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# libpq's sslmode values, which asyncpg takes as its ``ssl`` argument
_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def asyncpg_url(url: str) -> str:
    """Accept a plain Postgres URL as hosted providers (Neon, Supabase) hand
    them out, and turn it into one SQLAlchemy's asyncpg driver understands.

    ``postgres://`` and ``postgresql://`` become ``postgresql+asyncpg://``;
    ``sslmode=require`` becomes ``ssl=require``; ``channel_binding`` (which
    asyncpg doesn't take) is dropped.
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    if scheme != "postgresql+asyncpg":
        return url
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode" and value in _SSLMODES:
            query.append(("ssl", value))
        elif key != "channel_binding":
            query.append((key, value))
    return urlunsplit(parts._replace(scheme=scheme, query=urlencode(query)))


def celery_redis_url(url: str) -> str:
    """Celery refuses a TLS Redis URL (``rediss://``, as Upstash gives out)
    unless it says how to check the certificate; default to checking it."""
    if url.startswith("rediss://") and "ssl_cert_reqs=" not in url:
        return url + ("&" if "?" in url else "?") + "ssl_cert_reqs=CERT_REQUIRED"
    return url


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

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, v: str) -> str:
        return asyncpg_url(v)

    @field_validator("celery_broker_url", "celery_result_backend")
    @classmethod
    def _celery_redis_url(cls, v: str) -> str:
        return celery_redis_url(v)

    @field_validator("frame_ancestors")
    @classmethod
    def _origins_only(cls, v: list[str]) -> list[str]:
        # These go verbatim into the CSP header, so nothing but plain origins
        for origin in v:
            if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d{1,5})?", origin):
                raise ValueError(f"FRAME_ANCESTORS takes https origins only, got {origin!r}")
        return v

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
    # Run scheduled detection inside the API process instead of Celery beat,
    # for single-container hosting. It only runs after new logs arrive, so an
    # idle deployment never touches the database (serverless Postgres such as
    # Neon can then suspend).
    inprocess_scheduler: bool = False
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

    # ── Embedding ────────────────────────────────────
    # Origins allowed to show the dashboard in a frame, e.g.
    # ["https://huggingface.co"] for a Hugging Face Space page. Empty: none.
    frame_ancestors: list[str] = []


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()

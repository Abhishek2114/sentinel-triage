"""FastAPI application factory and router registration."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import get_settings
from app.core.db import Base, engine
from app.core.logging import get_logger, setup_logging
from app.realtime import redis_factory, relay

settings = get_settings()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown lifecycle hooks."""
    setup_logging()
    log.info("starting_sentinel_triage", version="0.1.0", debug=settings.debug)

    # Dev convenience only. With several uvicorn workers each one would race to
    # create the same tables, so Docker disables this and runs `alembic upgrade head`.
    if settings.auto_create_tables:
        async with engine.begin() as conn:
            # runbook_chunks.embedding needs pgvector (migration 0003 does the same)
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    # Forward incident events from Redis to this process's WebSocket clients
    relay_task: asyncio.Task[None] | None = None
    if settings.realtime_enabled:
        from app.api.ws import broadcast

        relay_task = asyncio.create_task(relay(redis_factory(settings.redis_url), broadcast))

    yield

    if relay_task is not None:
        relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay_task
    await engine.dispose()
    log.info("shutdown_complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Sentinel Triage",
        description="AI-powered log anomaly detection and incident triage",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS – allow the React dashboard in dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from app.api.routes_detect import router as detect_router
    from app.api.routes_events import router as events_router
    from app.api.routes_health import router as health_router
    from app.api.routes_incidents import router as incidents_router
    from app.api.routes_ingest import router as ingest_router
    from app.api.routes_triage import router as triage_router
    from app.api.ws import router as ws_router

    prefix = settings.api_v1_prefix

    app.include_router(health_router, prefix=prefix)
    app.include_router(events_router, prefix=prefix)
    app.include_router(incidents_router, prefix=prefix)
    app.include_router(ingest_router, prefix=prefix)
    app.include_router(detect_router, prefix=prefix)
    app.include_router(triage_router, prefix=prefix)
    app.include_router(ws_router, prefix=prefix)

    return app


app = create_app()

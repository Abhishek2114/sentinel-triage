"""FastAPI application factory and router registration."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import get_settings
from app.core.db import Base, engine
from app.core.logging import get_logger, setup_logging
from app.core.metrics import render as render_metrics
from app.core.security import (
    MetricsMiddleware,
    RequestGuardMiddleware,
    SecurityHeadersMiddleware,
    auth_enabled,
    require_key,
)
from app.realtime import redis_factory, relay

settings = get_settings()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown lifecycle hooks."""
    setup_logging()
    log.info("starting_sentinel_triage", version="0.1.0", debug=settings.debug)
    if not auth_enabled(settings):
        if settings.environment == "production":
            raise RuntimeError("ENVIRONMENT=production requires API_KEYS")
        log.warning("auth_disabled", hint="set API_KEYS before exposing this server")

    # Dev convenience only. With several uvicorn workers each one would race to
    # create the same tables, so Docker disables this and runs `alembic upgrade head`.
    if settings.auto_create_tables:
        async with engine.begin() as conn:
            # runbook_chunks.embedding needs pgvector (migration 0003 does the same)
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    # Load the embedding model now, in a thread, rather than on the first triage
    from app.rag.embeddings import get_embedder

    warmup = asyncio.create_task(asyncio.to_thread(get_embedder, settings.embedding_model))

    # Forward incident events from Redis to this process's WebSocket clients
    relay_task: asyncio.Task[None] | None = None
    if settings.realtime_enabled:
        from app.api.ws import broadcast

        relay_task = asyncio.create_task(relay(redis_factory(settings.redis_url), broadcast))

    # Single-container hosting: detection runs here instead of Celery beat
    scheduler_task: asyncio.Task[None] | None = None
    if settings.inprocess_scheduler:
        from app.scheduler import run_scheduler

        scheduler_task = asyncio.create_task(run_scheduler(redis_factory(settings.redis_url)))

    yield

    warmup.cancel()
    for task in (relay_task, scheduler_task):
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    await engine.dispose()
    log.info("shutdown_complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    production = settings.environment == "production"
    app = FastAPI(
        title="Sentinel Triage",
        description="AI-powered log anomaly detection and incident triage",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )

    # CORS – allow the React dashboard in dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added last = runs first: headers go on every response, 413/400 included
    app.add_middleware(RequestGuardMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Register routers
    from app.api.routes_demo import router as demo_router
    from app.api.routes_detect import router as detect_router
    from app.api.routes_events import router as events_router
    from app.api.routes_health import router as health_router
    from app.api.routes_incidents import router as incidents_router
    from app.api.routes_ingest import router as ingest_router
    from app.api.routes_overview import router as overview_router
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
    app.include_router(overview_router, prefix=prefix)
    app.include_router(demo_router, prefix=prefix)

    @app.get("/agent/sentinel_ship.py", include_in_schema=False)
    def agent() -> FileResponse:
        """The log-shipping agent, for the onboarding page's copy-paste commands."""
        return FileResponse(AGENT, media_type="text/x-python", filename="sentinel_ship.py")

    @app.get("/metrics", include_in_schema=False, dependencies=[Depends(require_key)])
    def metrics() -> Response:
        body, content_type = render_metrics()
        return Response(body, media_type=content_type)

    # The built dashboard, same origin as the API (so the CSP can stay 'self')
    if DASHBOARD_DIST.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")

    return app


ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIST = ROOT / "dashboard" / "dist"
AGENT = ROOT / "agent" / "sentinel_ship.py"


app = create_app()

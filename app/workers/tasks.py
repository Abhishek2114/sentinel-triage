"""Celery task definitions for detection and triage pipeline."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import Task
from celery.signals import worker_ready
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.detect.pipeline import DEFAULT_DETECTORS, DetectionSummary, run_detection
from app.rag.embeddings import get_embedder
from app.rag.index import index_runbooks as index_runbooks_into
from app.rag.summarizer import llm_from_settings
from app.realtime import publish_incident, redis_factory
from app.triage.service import TriageOutcome, triage
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    # Each task runs in its own event loop (asyncio.run), and asyncpg connections
    # can't be shared across loops, so use a throwaway engine without pooling.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _detect(
    since: datetime | None,
    until: datetime | None,
    service: str | None,
    window_minutes: int,
    detectors: list[str],
) -> DetectionSummary:
    async with _session() as session:
        return await run_detection(
            session,
            detectors=detectors,
            since=since,
            until=until,
            service=service,
            window_minutes=window_minutes,
        )


@celery_app.task(name="detect_anomalies", bind=True, max_retries=3, default_retry_delay=30)
def detect_anomalies(
    self: Task,
    service: str | None = None,
    window_minutes: int = 1,
    lookback_minutes: int | None = None,
    detectors: list[str] | None = None,
) -> dict[str, Any]:
    """Detect anomalies in stored logs, save them, and queue triage for new ones.

    With ``lookback_minutes`` (the scheduled mode) it scans the most recent
    complete windows; without it, every stored event. Only new anomalies from
    ``TRIAGE_DETECTORS`` are triaged.
    """
    since = until = None
    if lookback_minutes is not None:
        now = datetime.now(tz=UTC)
        # Stop at the last complete minute: a half-filled window looks like a traffic drop
        until = now.replace(second=0, microsecond=0)
        since = until - timedelta(minutes=lookback_minutes)

    try:
        summary = asyncio.run(
            _detect(since, until, service, window_minutes, detectors or list(DEFAULT_DETECTORS))
        )
    except (OSError, DBAPIError) as exc:  # database unreachable or restarting
        raise self.retry(exc=exc) from exc

    queued = [
        anomaly_id
        for detector in get_settings().triage_detectors
        for anomaly_id in summary.new_ids_by_detector.get(detector, [])
    ]
    for anomaly_id in queued:
        triage_anomaly.delay(anomaly_id)

    return {
        "services": summary.services,
        "windows_scored": summary.windows_scored,
        "anomalies": len(summary.anomaly_ids),
        "new_anomalies": len(summary.new_anomaly_ids),
        "triage_queued": len(queued),
    }


async def _triage(anomaly_id: int) -> TriageOutcome:
    settings = get_settings()
    embedder = await asyncio.to_thread(get_embedder, settings.embedding_model)
    async with _session() as session:
        outcome = await triage(
            session,
            anomaly_id,
            embedder=embedder,
            llm=llm_from_settings(settings),
            group_gap=timedelta(minutes=settings.incident_group_minutes),
        )
        if outcome.event is not None:
            # Best effort: a Redis outage is logged, never retried as a failed triage
            await publish_incident(
                session, redis_factory(settings.redis_url), outcome.incident_id, outcome.event
            )
        return outcome


@celery_app.task(name="triage_anomaly", bind=True, max_retries=3, default_retry_delay=30)
def triage_anomaly(self: Task, anomaly_id: int) -> dict[str, Any]:
    """Attach one anomaly to an open incident, or open a new one, and announce it.

    LLM failures don't fail the task – it falls back to a rule-based report –
    so retries are only for the database being unavailable.
    """
    try:
        outcome = asyncio.run(_triage(anomaly_id))
    except LookupError:
        # Deleted between detection and triage; nothing to do
        return {"status": "missing", "anomaly_id": anomaly_id}
    except (OSError, DBAPIError) as exc:
        raise self.retry(exc=exc) from exc
    return {
        "status": outcome.action,
        "anomaly_id": anomaly_id,
        "incident_id": outcome.incident_id,
        "method": outcome.method,
    }


async def _index() -> int:
    settings = get_settings()
    embedder = await asyncio.to_thread(get_embedder, settings.embedding_model)
    async with _session() as session:
        return await index_runbooks_into(session, embedder, settings.runbooks_dir)


@celery_app.task(name="index_runbooks", bind=True, max_retries=5, default_retry_delay=10)
def index_runbooks(self: Task) -> dict[str, Any]:
    """Embed the runbooks into pgvector (replaces this embedder's chunks)."""
    try:
        return {"chunks": asyncio.run(_index())}
    except (OSError, DBAPIError) as exc:
        raise self.retry(exc=exc) from exc


@worker_ready.connect
def _index_runbooks_on_start(**_: Any) -> None:
    # Runbooks are files baked into the image, so re-index whenever a worker starts
    log.info("queue_runbook_index")
    index_runbooks.delay()

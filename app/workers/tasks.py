"""Celery task definitions for detection and triage pipeline."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import Task
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.detect.pipeline import DEFAULT_DETECTORS, DetectionSummary, run_detection
from app.workers.celery_app import celery_app


async def _detect(
    since: datetime | None,
    until: datetime | None,
    service: str | None,
    window_minutes: int,
    detectors: list[str],
) -> DetectionSummary:
    # Each task runs in its own event loop (asyncio.run), and asyncpg connections
    # can't be shared across loops, so use a throwaway engine without pooling.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await run_detection(
                session,
                detectors=detectors,
                since=since,
                until=until,
                service=service,
                window_minutes=window_minutes,
            )
    finally:
        await engine.dispose()


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
    complete windows; without it, every stored event.
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

    for anomaly_id in summary.new_anomaly_ids:
        triage_anomaly.delay(anomaly_id)

    return {
        "services": summary.services,
        "windows_scored": summary.windows_scored,
        "anomalies": len(summary.anomaly_ids),
        "new_anomalies": len(summary.new_anomaly_ids),
    }


@celery_app.task(name="triage_anomaly", bind=True, max_retries=3)
def triage_anomaly(self: Task, anomaly_id: int) -> dict[str, Any]:
    """Run RAG triage for a single anomaly.

    Phase 4 will fill in the full implementation:
      1. Fetch anomaly and related log events.
      2. Retrieve relevant runbook chunks.
      3. Ask LLM for summary and root cause.
      4. Save Incident to DB.
      5. Push incident over WebSocket.
    """
    # TODO: Phase 4 implementation
    return {"status": "not_implemented", "anomaly_id": anomaly_id}

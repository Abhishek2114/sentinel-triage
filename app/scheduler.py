"""Detection and triage inside the API process, for single-container hosting.

Celery beat runs detection on a timer whether or not anything arrived. Here a
loop wakes on the same interval but only works when this process has ingested
logs since its last run, so an idle deployment sends no queries at all and a
serverless database (Neon) can suspend.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.logging import get_logger
from app.detect.pipeline import run_detection
from app.rag.embeddings import Embedder, get_embedder
from app.rag.summarizer import llm_from_settings
from app.realtime import RedisFactory, publish_incident
from app.triage.service import triage

log = get_logger(__name__)

# When this process last stored logs; None once every log has been scanned
_last_ingest: datetime | None = None


def note_ingest(now: datetime | None = None) -> None:
    """Called by /ingest after a commit: there is something new to scan."""
    global _last_ingest
    _last_ingest = now or datetime.now(tz=UTC)


async def detect_and_triage(
    since: datetime,
    until: datetime,
    *,
    embedder: Embedder,
    redis: RedisFactory,
    detectors: list[str],
    triage_detectors: list[str],
) -> int:
    """Detect over [since, until), triage the new anomalies, announce incidents.

    Returns how many anomalies were triaged.
    """
    settings = get_settings()
    triaged = 0
    async with async_session_factory() as session:
        summary = await run_detection(
            session,
            detectors=detectors,
            since=since,
            until=until,
            max_services=settings.max_services_per_detection,
        )
        for detector in triage_detectors:
            for anomaly_id in summary.new_ids_by_detector.get(detector, []):
                outcome = await triage(
                    session,
                    anomaly_id,
                    embedder=embedder,
                    llm=llm_from_settings(settings, async_session_factory),
                    group_gap=timedelta(minutes=settings.incident_group_minutes),
                )
                triaged += 1
                if outcome.event is not None:
                    await publish_incident(session, redis, outcome.incident_id, outcome.event)
    return triaged


async def run_once(redis: RedisFactory, now: datetime | None = None) -> bool:
    """One scheduler tick. Returns whether detection ran."""
    global _last_ingest
    if _last_ingest is None:
        return False
    settings = get_settings()
    now = now or datetime.now(tz=UTC)
    # Stop at the last complete minute: a half-filled window looks like a traffic drop
    until = now.replace(second=0, microsecond=0)
    since = until - timedelta(minutes=settings.detection_lookback_minutes)
    seen = _last_ingest
    embedder = await asyncio.to_thread(get_embedder, settings.embedding_model)
    triaged = await detect_and_triage(
        since,
        until,
        embedder=embedder,
        redis=redis,
        detectors=settings.scheduled_detectors,
        triage_detectors=settings.triage_detectors,
    )
    # Logs that landed in the still-open minute get another pass next tick
    if _last_ingest == seen and seen < until:
        _last_ingest = None
    log.info(
        "scheduled_detection", since=since.isoformat(), until=until.isoformat(), triaged=triaged
    )
    return True


async def run_scheduler(redis: RedisFactory) -> None:
    """Tick every DETECTION_INTERVAL_SECONDS until cancelled."""
    interval = get_settings().detection_interval_seconds
    log.info("inprocess_scheduler_started", interval_seconds=interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_once(redis)
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the schedule alive through a bad run
            log.exception("scheduled_detection_failed")

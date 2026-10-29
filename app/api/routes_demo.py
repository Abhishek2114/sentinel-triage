"""The "Run demo" button: replay a synthetic incident end to end.

Generates an hour of SYNTHETIC logs for five ``demo-`` services ending now,
with one injected fault, then ingests, detects and triages them exactly as
real logs would be. Incidents appear on the dashboard over the WebSocket.

In DEMO_MODE anonymous visitors may run it; otherwise it needs an API key.
It is globally rate limited (one run per DEMO_COOLDOWN_SECONDS across all
processes) and replaces the previous demo data, so the database stays small.

The baseline misses about one demo fault in ten (memory leaks most often),
and a demo that ends in nothing shows nothing, so the button replays a run
whose fault is caught. Misses are counted by the evaluation, not here.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_embedder_dep, get_redis_factory
from app.core.config import get_settings
from app.core.db import get_session
from app.core.logging import get_logger
from app.core.security import require_key
from app.detect.features import EventPoint
from app.detect.pipeline import detect_events
from app.ingest.pipeline import PreparedBatch, prepare_lines
from app.models.anomaly import Anomaly
from app.models.event import LogEvent
from app.models.incident import Incident
from app.rag.embeddings import Embedder
from app.rag.index import count_chunks, index_runbooks
from app.realtime import RedisFactory
from app.scheduler import detect_and_triage
from app.schemas import DemoRunResponse

router = APIRouter(tags=["demo"])
log = get_logger(__name__)

PREFIX = "demo-"
_LOCK_KEY = 0x5E7D0  # pg advisory lock id for "a demo run is starting"
_tasks: set[asyncio.Task[None]] = set()
# Runs to draw before settling for one whose fault the baseline misses
_MAX_DRAWS = 5


async def _allow(request: Request) -> None:
    if not get_settings().demo_mode:
        await require_key(request)


def _synthetic_run(start: datetime) -> tuple[PreparedBatch, dict[str, Any]]:
    """An hour of demo logs from ``start``, and its fault, which the baseline catches."""
    from scripts.generate_logs import generate_logs

    rng = random.SystemRandom()
    for _ in range(_MAX_DRAWS):
        lines, labels = generate_logs(
            duration_hours=1,
            fault_count=1,
            seed=rng.randrange(1_000_000),
            start_time=start,
            service_prefix=PREFIX,
        )
        prepared, fault = prepare_lines(lines), labels[0]
        points = [
            EventPoint(
                cast(datetime, r["timestamp"]),
                str(r["service"]),
                str(r["level"]),
                cast(str | None, r["template_id"]),
            )
            for r in prepared.rows
        ]
        found = detect_events(points, ["error_burst"])
        if any(a.service == fault["service"] for a in found.anomalies):
            break
    return prepared, fault


async def _clear_demo_data(session: AsyncSession) -> None:
    demo = Anomaly.service.like(f"{PREFIX}%")
    await session.execute(update(Anomaly).where(demo).values(incident_id=None))
    await session.execute(delete(Incident).where(Incident.service.like(f"{PREFIX}%")))
    await session.execute(delete(Anomaly).where(demo))
    await session.execute(delete(LogEvent).where(LogEvent.service.like(f"{PREFIX}%")))


async def _detect_and_triage(
    since: datetime, until: datetime, embedder: Embedder, redis: RedisFactory
) -> None:
    try:
        await detect_and_triage(
            since,
            until,
            embedder=embedder,
            redis=redis,
            detectors=["error_burst"],
            triage_detectors=["error_burst"],
        )
    except Exception:  # background task: log, never crash the server
        log.exception("demo_run_failed")


@router.post(
    "/demo/run",
    response_model=DemoRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_allow)],
)
async def run_demo(
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder_dep),
    redis: RedisFactory = Depends(get_redis_factory),
) -> DemoRunResponse:
    settings = get_settings()
    # One starter at a time across every process; the lock ends with the transaction
    locked = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    if not locked:
        raise HTTPException(status_code=429, detail="A demo run is already starting")
    last = await session.scalar(
        select(func.max(LogEvent.created_at)).where(LogEvent.service.like(f"{PREFIX}%"))
    )
    now = datetime.now(tz=UTC)
    if last is not None and now - last < timedelta(seconds=settings.demo_cooldown_seconds):
        wait = settings.demo_cooldown_seconds - int((now - last).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=f"The demo ran moments ago; try again in {wait}s",
            headers={"Retry-After": str(max(wait, 1))},
        )

    start = now.replace(second=0, microsecond=0) - timedelta(minutes=60)
    prepared, fault = await asyncio.to_thread(_synthetic_run, start)
    await _clear_demo_data(session)
    for i in range(0, len(prepared.rows), 1000):
        await session.execute(insert(LogEvent), prepared.rows[i : i + 1000])
    await session.commit()

    if await count_chunks(session, embedder.name) == 0:
        await index_runbooks(session, embedder, settings.runbooks_dir)

    task = asyncio.create_task(_detect_and_triage(start, now, embedder, redis))
    _tasks.add(task)  # keep a reference so it isn't garbage-collected mid-run
    task.add_done_callback(_tasks.discard)

    log.info("demo_started", lines=len(prepared.rows), fault=fault["fault_type"])
    return DemoRunResponse(
        status="started",
        lines=len(prepared.rows),
        services=sorted({str(r["service"]) for r in prepared.rows}),
        fault=str(fault["fault_type"]),
        detail=f"Synthetic {fault['fault_type'].replace('_', ' ')} injected on "
        f"{fault['service']}. Detection and triage are running; the incident "
        "appears here in a few seconds.",
    )

"""Detection pipeline: stored events → per-service windows → detectors → anomalies.

The pure part (``detect_service``) works on in-memory windows so it can be
unit-tested and evaluated without a database. ``run_detection`` adds the
database reads and idempotent writes, and is shared by the API and Celery.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from sqlalchemy import Boolean, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.detect.base import AnomalyResult, Detector
from app.detect.error_burst import ErrorBurstDetector
from app.detect.features import EventPoint, WindowFeatures, aggregate_windows
from app.detect.iforest import IForestDetector
from app.models.anomaly import Anomaly
from app.models.event import LogEvent

log = get_logger(__name__)

# Fresh instance per run and per service: each service learns its own "normal"
DETECTORS: dict[str, Callable[[], Detector]] = {
    ErrorBurstDetector.name: ErrorBurstDetector,
    IForestDetector.name: IForestDetector,
}
DEFAULT_DETECTORS: tuple[str, ...] = tuple(DETECTORS)

# In the first windows of a series every template counts as "new", which looks
# like a bad deploy. Those windows are used for fitting but never reported.
WARMUP_WINDOWS = 5


def merge_anomalous_windows(
    results: Sequence[AnomalyResult],
    max_gap_windows: int = 1,
) -> list[AnomalyResult]:
    """Collapse runs of flagged windows into one anomaly each.

    A 10-minute fault would otherwise become 10 anomalies and, later, 10
    incidents and 10 LLM calls. Runs separated by up to ``max_gap_windows``
    quiet windows are joined. The merged score is the run's peak score.
    """
    merged: list[AnomalyResult] = []
    gap = 0
    for result in results:
        if not result.is_anomaly:
            gap += 1
            continue
        if merged and gap <= max_gap_windows:
            last = merged[-1]
            merged[-1] = replace(
                last, window_end=result.window_end, score=max(last.score, result.score)
            )
        else:
            merged.append(result)
        gap = 0
    return merged


def detect_service(
    windows: list[WindowFeatures],
    detector: Detector,
    warmup_windows: int = WARMUP_WINDOWS,
) -> list[AnomalyResult]:
    """Run one detector over one service's windows and return merged anomalies."""
    results = detector.detect(windows)
    return merge_anomalous_windows(results[warmup_windows:])


@dataclass
class DetectionSummary:
    """What a detection run did."""

    services: list[str] = field(default_factory=list)
    windows_scored: int = 0
    anomaly_ids: list[int] = field(default_factory=list)
    new_anomaly_ids: list[int] = field(default_factory=list)


async def load_events(
    session: AsyncSession,
    since: datetime | None = None,
    until: datetime | None = None,
    service: str | None = None,
) -> list[EventPoint]:
    """Load only the columns detection needs, oldest first."""
    stmt = select(LogEvent.timestamp, LogEvent.service, LogEvent.level, LogEvent.template_id)
    if since is not None:
        stmt = stmt.where(LogEvent.timestamp >= since)
    if until is not None:
        stmt = stmt.where(LogEvent.timestamp < until)
    if service is not None:
        stmt = stmt.where(LogEvent.service == service)
    rows = await session.execute(stmt.order_by(LogEvent.timestamp))
    return [EventPoint(*row) for row in rows]


async def run_detection(
    session: AsyncSession,
    *,
    detectors: Sequence[str] = DEFAULT_DETECTORS,
    since: datetime | None = None,
    until: datetime | None = None,
    service: str | None = None,
    window_minutes: int = 1,
) -> DetectionSummary:
    """Detect anomalies in stored events and upsert them into ``anomalies``.

    Idempotent: re-running over the same period updates existing rows (matched
    on detector + service + window_start) instead of inserting duplicates, and
    an existing anomaly's end and peak score only ever grow.
    """
    unknown = set(detectors) - DETECTORS.keys()
    if unknown:
        raise ValueError(f"Unknown detector(s): {sorted(unknown)}")

    events = await load_events(session, since, until, service)
    by_service: dict[str, list[EventPoint]] = defaultdict(list)
    for event in events:
        by_service[event.service].append(event)

    summary = DetectionSummary(services=sorted(by_service))
    rows: list[dict[str, object]] = []

    for svc in summary.services:
        windows = aggregate_windows(by_service[svc], window_minutes=window_minutes, service=svc)
        summary.windows_scored += len(windows)
        for name in detectors:
            for anomaly in detect_service(windows, DETECTORS[name]()):
                rows.append({
                    "detector": name,
                    "service": svc,
                    "score": anomaly.score,
                    "window_start": anomaly.window_start,
                    "window_end": anomaly.window_end,
                })

    if rows:
        insert_stmt = insert(Anomaly).values(rows)
        upsert = insert_stmt.on_conflict_do_update(
            constraint="uq_anomalies_detector_service_window",
            # Only ever extend: a run over a shorter range (e.g. a rolling
            # lookback) mustn't cut short an anomaly an earlier run saw fully
            set_={
                "window_end": func.greatest(Anomaly.window_end, insert_stmt.excluded.window_end),
                "score": func.greatest(Anomaly.score, insert_stmt.excluded.score),
            },
        ).returning(
            Anomaly.id,
            # Postgres sets xmax = 0 on freshly inserted rows, non-zero on updated ones
            literal_column("xmax = 0", Boolean).label("inserted"),
        )
        for anomaly_id, inserted in (await session.execute(upsert)).all():
            summary.anomaly_ids.append(anomaly_id)
            if inserted:
                summary.new_anomaly_ids.append(anomaly_id)

    await session.commit()

    log.info(
        "detection_complete",
        services=len(summary.services),
        windows=summary.windows_scored,
        anomalies=len(summary.anomaly_ids),
        new=len(summary.new_anomaly_ids),
    )
    return summary

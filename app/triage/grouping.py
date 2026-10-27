"""Group related anomalies into one incident.

A fault that flaps (flagged, quiet for a few minutes, flagged again) produces
several anomalies. Without grouping each becomes its own incident, its own LLM
call and its own page. The rule: an anomaly joins an *open* incident on the
same service if it is within ``gap`` of that incident's time span.

``joins`` is the rule in plain Python (used by the evaluation);
``find_open_incident`` is the same rule as a query.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TypeVar

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.detect.pipeline import FoundAnomaly
from app.models.anomaly import Anomaly
from app.models.incident import Incident


def joins(
    started_at: datetime,
    last_seen_at: datetime,
    window_start: datetime,
    window_end: datetime,
    gap: timedelta,
) -> bool:
    """Does an anomaly window fall within ``gap`` of an incident's span?"""
    return window_start <= last_seen_at + gap and window_end >= started_at - gap


A = TypeVar("A", bound=FoundAnomaly)


def group_anomalies(anomalies: Sequence[A], gap: timedelta) -> list[list[A]]:
    """Group anomalies the way triage would, processing them in time order.

    Anomalies on different services never share a group.
    """
    groups: list[list[A]] = []
    spans: list[tuple[str, datetime, datetime]] = []
    for a in sorted(anomalies, key=lambda x: x.window_start):
        for i, (service, start, end) in enumerate(spans):
            if service == a.service and joins(start, end, a.window_start, a.window_end, gap):
                groups[i].append(a)
                spans[i] = (service, min(start, a.window_start), max(end, a.window_end))
                break
        else:
            groups.append([a])
            spans.append((a.service, a.window_start, a.window_end))
    return groups


async def lock_service(session: AsyncSession, service: str | None) -> None:
    """Serialise incident decisions per service until the transaction ends.

    Two workers triaging anomalies for the same service at the same moment
    would otherwise both find no open incident and both open one.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"incident:{service}"},
    )


async def find_open_incident(session: AsyncSession, anomaly: Anomaly, gap: timedelta) -> int | None:
    """The open incident this anomaly should join, if any (most recent first)."""
    return await session.scalar(
        select(Incident.id)
        .where(
            Incident.status == "open",
            Incident.service.is_not_distinct_from(anomaly.service),
            Incident.last_seen_at + gap >= anomaly.window_start,
            Incident.started_at - gap <= anomaly.window_end,
        )
        .order_by(Incident.last_seen_at.desc())
        .limit(1)
    )


async def attach(session: AsyncSession, incident_id: int, anomaly: Anomaly) -> bool:
    """Link the anomaly and widen the incident's span. False if already linked."""
    linked = await session.scalar(
        update(Anomaly)
        .where(Anomaly.id == anomaly.id, Anomaly.incident_id.is_(None))
        .values(incident_id=incident_id)
        .returning(Anomaly.id)
    )
    if linked is None:
        return False
    await session.execute(
        update(Incident)
        .where(Incident.id == incident_id)
        .values(
            anomaly_count=Incident.anomaly_count + 1,
            started_at=func.least(Incident.started_at, anomaly.window_start),
            last_seen_at=func.greatest(Incident.last_seen_at, anomaly.window_end),
            updated_at=func.now(),
        )
    )
    return True

"""Incident and anomaly query endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_redis_factory
from app.core.db import get_session
from app.core.security import require_key, require_read_access
from app.models.anomaly import Anomaly
from app.models.event import LogEvent
from app.models.incident import Incident
from app.realtime import RedisFactory, publish_incident
from app.schemas import (
    AnomalyRead,
    IncidentDetail,
    IncidentRead,
    IncidentStatus,
    IncidentUpdate,
    LogEventRead,
    SimilarIncident,
)
from app.triage.memory import incident_patterns, similar_incidents

router = APIRouter(tags=["incidents"])


# ── Anomalies ─────────────────────────────────────────


@router.get(
    "/anomalies",
    response_model=list[AnomalyRead],
    dependencies=[Depends(require_read_access)],
)
async def list_anomalies(
    detector: str | None = Query(None),
    service: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Anomaly]:
    """List detected anomalies, newest window first, optionally filtered."""
    stmt = select(Anomaly).order_by(Anomaly.window_start.desc(), Anomaly.id.desc())
    if detector:
        stmt = stmt.where(Anomaly.detector == detector)
    if service:
        stmt = stmt.where(Anomaly.service == service)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Incidents ─────────────────────────────────────────


@router.get(
    "/incidents",
    response_model=list[IncidentRead],
    dependencies=[Depends(require_read_access)],
)
async def list_incidents(
    status: IncidentStatus | None = Query(None),
    service: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Incident]:
    """List incidents, most recently active first."""
    stmt = select(Incident).order_by(Incident.last_seen_at.desc(), Incident.id.desc())
    if status:
        stmt = stmt.where(Incident.status == status)
    if service:
        stmt = stmt.where(Incident.service == service)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _load_incident(session: AsyncSession, incident_id: int) -> Incident:
    incident = await session.scalar(
        select(Incident).options(selectinload(Incident.anomalies)).where(Incident.id == incident_id)
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentDetail,
    dependencies=[Depends(require_read_access)],
)
async def get_incident(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    """An incident with its grouped anomalies and the log lines it cites."""
    incident = await _load_incident(session, incident_id)

    related_events: list[LogEvent] = []
    if incident.related_event_ids:
        evt_stmt = (
            select(LogEvent)
            .where(LogEvent.id.in_(incident.related_event_ids))
            .order_by(LogEvent.timestamp)
        )
        evt_result = await session.execute(evt_stmt)
        related_events = list(evt_result.scalars().all())

    detail = IncidentDetail.model_validate(incident)
    detail.related_events = [LogEventRead.model_validate(e) for e in related_events]
    detail.similar_incidents = [
        SimilarIncident.model_validate(p)
        for p in await similar_incidents(
            session, incident_patterns(incident.evidence), exclude_id=incident.id
        )
    ]
    return detail


@router.patch(
    "/incidents/{incident_id}",
    response_model=IncidentRead,
    dependencies=[Depends(require_key)],
)
async def update_incident(
    incident_id: int,
    body: IncidentUpdate,
    session: AsyncSession = Depends(get_session),
    redis: RedisFactory = Depends(get_redis_factory),
) -> Incident:
    """Resolve or reopen an incident. Resolved incidents no longer absorb new anomalies."""
    incident = await _load_incident(session, incident_id)
    resolving = body.status == "resolved"
    note_changed = resolving and body.resolution is not None
    if incident.status != body.status or note_changed:
        values: dict[str, object] = {"status": body.status, "updated_at": func.now()}
        if resolving and incident.status != "resolved":
            values["resolved_at"] = func.now()
        if not resolving:
            values["resolved_at"] = None
        if body.resolution is not None:
            values["resolution"] = body.resolution.strip() or None
        await session.execute(update(Incident).where(Incident.id == incident_id).values(**values))
        await session.commit()
        await publish_incident(session, redis, incident_id, "incident.updated")
    # populate_existing: the session already holds this row with the old status
    refreshed = await session.scalar(
        select(Incident).where(Incident.id == incident_id).execution_options(populate_existing=True)
    )
    assert refreshed is not None
    return refreshed

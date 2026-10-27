"""Incident and anomaly query endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.db import get_session
from app.models.anomaly import Anomaly
from app.models.event import LogEvent
from app.models.incident import Incident
from app.schemas import AnomalyRead, IncidentDetail, IncidentRead, LogEventRead

router = APIRouter(tags=["incidents"])


# ── Anomalies ─────────────────────────────────────────


@router.get("/anomalies", response_model=list[AnomalyRead])
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


@router.get("/incidents", response_model=list[IncidentRead])
async def list_incidents(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Incident]:
    """List triaged incidents."""
    stmt = select(Incident).order_by(Incident.created_at.desc())
    if status:
        stmt = stmt.where(Incident.status == status)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    """Get a single incident with its linked anomaly and related log events."""
    stmt = (
        select(Incident)
        .options(joinedload(Incident.anomaly))
        .where(Incident.id == incident_id)
    )
    result = await session.execute(stmt)
    incident = result.scalar_one_or_none()

    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Fetch related events if IDs are present
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
    return detail

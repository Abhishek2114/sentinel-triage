"""Log event query endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.ingest.parser import normalise_level
from app.models.event import LogEvent
from app.schemas import LogEventRead

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[LogEventRead])
async def list_events(
    service: str | None = Query(None),
    level: str | None = Query(None),
    since: datetime | None = Query(None, description="ISO-8601 lower bound"),
    until: datetime | None = Query(None, description="ISO-8601 upper bound"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[LogEvent]:
    """Query stored log events with optional filters."""
    stmt = select(LogEvent).order_by(LogEvent.timestamp.desc())

    if service:
        stmt = stmt.where(LogEvent.service == service)
    if level:
        # Stored levels are canonical, so "warn" must match "WARNING"
        stmt = stmt.where(LogEvent.level == normalise_level(level))
    if since:
        stmt = stmt.where(LogEvent.timestamp >= since)
    if until:
        stmt = stmt.where(LogEvent.timestamp <= until)

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())

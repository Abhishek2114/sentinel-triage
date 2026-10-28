"""Log event query endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_read_access
from app.ingest.parser import normalise_level
from app.models.event import LogEvent
from app.schemas import LogEventRead

router = APIRouter(
    prefix="/events", tags=["events"], dependencies=[Depends(require_read_access)]
)


@router.get("", response_model=list[LogEventRead])
async def list_events(
    service: str | None = Query(None),
    level: str | None = Query(None),
    since: datetime | None = Query(None, description="ISO-8601 lower bound"),
    until: datetime | None = Query(None, description="ISO-8601 upper bound"),
    q: str | None = Query(None, max_length=200, description="Case-insensitive text match"),
    after_id: int | None = Query(None, description="Only events stored after this id (tailing)"),
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
    if q:
        # Escape LIKE wildcards so the text is matched literally
        pattern = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(LogEvent.message.ilike(f"%{pattern}%", escape="\\"))
    if after_id is not None:
        stmt = stmt.where(LogEvent.id > after_id)

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{event_id}/context", response_model=list[LogEventRead])
async def event_context(
    event_id: int,
    before: int = Query(20, ge=0, le=200),
    after: int = Query(20, ge=0, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[LogEvent]:
    """The lines around one event on the same service, in time order."""
    event = await session.get(LogEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    key = (LogEvent.timestamp, LogEvent.id)
    earlier = or_(
        LogEvent.timestamp < event.timestamp,
        and_(LogEvent.timestamp == event.timestamp, LogEvent.id < event.id),
    )
    later = or_(
        LogEvent.timestamp > event.timestamp,
        and_(LogEvent.timestamp == event.timestamp, LogEvent.id > event.id),
    )
    same = LogEvent.service == event.service
    prev = await session.scalars(
        select(LogEvent).where(same, earlier).order_by(*(k.desc() for k in key)).limit(before)
    )
    nxt = await session.scalars(select(LogEvent).where(same, later).order_by(*key).limit(after))
    return [*reversed(list(prev)), event, *nxt]

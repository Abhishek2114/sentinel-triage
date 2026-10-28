""""Is anything wrong?": a plain-language verdict and a per-service board."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import auth_enabled, reads_are_public, require_read_access
from app.models.event import LogEvent
from app.models.incident import Incident
from app.models.llm_usage import LLMUsage
from app.schemas import (
    IncidentRead,
    Overview,
    PublicConfig,
    ServiceHealth,
    ServiceStatus,
    Verdict,
)

router = APIRouter(tags=["overview"])

MAX_BOARD_SERVICES = 60
_ERROR_LEVELS = ("ERROR", "CRITICAL", "FATAL")
# "Elevated" needs both: a real share of errors and more than a handful
ELEVATED_RATE = 0.05
ELEVATED_MIN_ERRORS = 5


_ACRONYMS = {"db", "tls", "dns", "cpu"}


def runbook_title(runbook: str | None) -> str | None:
    """"db_connection_failure.md" → "DB connection failure" (same as the dashboard)."""
    if not runbook:
        return None
    first, *rest = runbook.removesuffix(".md").split("_")
    first = first.upper() if first in _ACRONYMS else first.capitalize()
    return " ".join([first, *rest])


def service_status(errors: int, events: int, has_incident: bool) -> ServiceStatus:
    if has_incident:
        return "incident"
    if events == 0:
        return "quiet"
    if errors >= ELEVATED_MIN_ERRORS and errors / events >= ELEVATED_RATE:
        return "elevated"
    return "healthy"


def verdict(
    services: list[ServiceHealth], incidents: list[IncidentRead], window: int
) -> Verdict:
    if incidents:
        parts = []
        for inc in incidents[:3]:
            cause = runbook_title(inc.runbook)
            if cause and not cause.split()[0].isupper():
                cause = cause[0].lower() + cause[1:]  # mid-sentence: "likely bad deploy"
            since = inc.started_at.strftime("%H:%M UTC")
            parts.append(
                f"{inc.service or 'all services'}"
                + (f" (likely {cause})" if cause else "")
                + f", since {since}"
            )
        more = f" and {len(incidents) - 3} more" if len(incidents) > 3 else ""
        noun = "incident" if len(incidents) == 1 else "incidents"
        return Verdict(
            level="incident",
            headline=f"{len(incidents)} open {noun}",
            detail="; ".join(parts) + more + ".",
        )
    elevated = [s.service for s in services if s.status == "elevated"]
    if elevated:
        names = ", ".join(elevated[:3]) + (" and others" if len(elevated) > 3 else "")
        return Verdict(
            level="warning",
            headline="No open incidents, error rates elevated",
            detail=f"Error rates are above normal on {names} but no detector has fired.",
        )
    reporting = [s for s in services if s.events]
    if not reporting:
        return Verdict(
            level="no_data",
            headline="No recent logs",
            detail=f"Nothing received in the last {window} minutes. Is a log shipper running?",
        )
    return Verdict(
        level="ok",
        headline="All clear",
        detail=f"{len(reporting)} services reporting, no open incidents, "
        f"no elevated error rates in the last {window} minutes.",
    )


@router.get("/overview", response_model=Overview, dependencies=[Depends(require_read_access)])
async def overview(
    window_minutes: int = Query(30, ge=5, le=240),
    session: AsyncSession = Depends(get_session),
) -> Overview:
    now = datetime.now(tz=UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    since = now - timedelta(minutes=window_minutes)
    anchored = False
    recent = await session.scalar(select(func.count()).where(LogEvent.timestamp >= since))
    if not recent:
        latest = await session.scalar(select(func.max(LogEvent.timestamp)))
        if latest is not None:
            # Replayed or historical logs: show the newest window there is
            anchored = True
            now = latest.replace(second=0, microsecond=0) + timedelta(minutes=1)
            since = now - timedelta(minutes=window_minutes)

    minute = func.date_trunc("minute", LogEvent.timestamp)
    is_error = LogEvent.level.in_(_ERROR_LEVELS)
    rows = await session.execute(
        select(
            LogEvent.service,
            minute,
            func.count(),
            func.count().filter(is_error),
            func.max(LogEvent.timestamp),
        )
        .where(LogEvent.timestamp >= since, LogEvent.timestamp < now)
        .group_by(LogEvent.service, minute)
    )
    per_service: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    last_seen: dict[str, datetime] = {}
    for service, bucket, n, errs, latest in rows.tuples():
        idx = int((bucket - since).total_seconds() // 60)
        if 0 <= idx < window_minutes:
            per_service[service][idx] = (n, errs)
        last_seen[service] = max(latest, last_seen.get(service, latest))

    open_rows = (
        await session.scalars(
            select(Incident)
            .where(Incident.status == "open")
            .order_by(Incident.last_seen_at.desc(), Incident.id.desc())
            .limit(50)
        )
    ).all()
    incidents = [IncidentRead.model_validate(i) for i in open_rows]
    incident_by_service = {i.service: i.id for i in reversed(incidents) if i.service}

    names = set(per_service) | set(incident_by_service)
    board: list[ServiceHealth] = []
    for name in names:
        buckets = per_service.get(name, {})
        events = [buckets.get(i, (0, 0))[0] for i in range(window_minutes)]
        errors = [buckets.get(i, (0, 0))[1] for i in range(window_minutes)]
        total, errs = sum(events), sum(errors)
        board.append(
            ServiceHealth(
                service=name,
                status=service_status(errs, total, name in incident_by_service),
                events=total,
                errors=errs,
                error_rate=round(errs / total, 4) if total else 0.0,
                last_seen=last_seen.get(name),
                open_incident_id=incident_by_service.get(name),
                errors_per_minute=errors,
                events_per_minute=events,
            )
        )
    rank = {"incident": 0, "elevated": 1, "healthy": 2, "quiet": 3}
    board.sort(key=lambda s: (rank[s.status], -s.errors, s.service))

    return Overview(
        verdict=verdict(board, incidents, window_minutes),
        as_of=now,
        anchored=anchored,
        window_minutes=window_minutes,
        services=board[:MAX_BOARD_SERVICES],
        services_truncated=len(board) > MAX_BOARD_SERVICES,
        open_incidents=incidents,
    )


@router.get("/config", response_model=PublicConfig)
async def public_config(session: AsyncSession = Depends(get_session)) -> PublicConfig:
    """What the dashboard needs before it can show anything. Public on purpose."""
    settings = get_settings()
    provider = None
    if settings.llm_provider == "anthropic":
        provider = "anthropic"
    elif settings.openai_api_key or settings.llm_base_url:
        provider = "openai"
    today = datetime.now(tz=UTC).date()
    calls = await session.scalar(select(LLMUsage.calls).where(LLMUsage.day == today))
    return PublicConfig(
        version="0.2.0",
        demo_mode=settings.demo_mode,
        read_requires_key=not reads_are_public(settings),
        write_requires_key=auth_enabled(settings),
        llm_provider=provider,
        llm_calls_today=calls or 0,
        llm_calls_limit=settings.llm_max_calls_per_day,
    )

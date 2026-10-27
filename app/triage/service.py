"""Triage one anomaly: join an open incident, or evidence → runbooks → report → new incident.

Shared by the Celery task (automatic, for new anomalies) and the
``POST /anomalies/{id}/triage`` endpoint (on demand).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.anomaly import Anomaly
from app.models.event import LogEvent
from app.models.incident import Incident
from app.rag.embeddings import Embedder
from app.rag.retriever import runbook_context
from app.rag.summarizer import LLMClient, TriageContext, summarize
from app.realtime import EventType
from app.triage.evidence import (
    BASELINE_PERIOD,
    Evidence,
    EvidenceEvent,
    build_evidence,
    evidence_window,
)
from app.triage.grouping import attach, find_open_incident, lock_service

log = get_logger(__name__)

# Bounds the work and the prompt size for a very noisy window
MAX_WINDOW_EVENTS = 20_000


DEFAULT_GROUP_GAP = timedelta(minutes=15)


@dataclass
class TriageOutcome:
    incident_id: int
    # "created": opened a new incident; "grouped": joined an open one;
    # "existing": this anomaly was already triaged
    action: Literal["created", "grouped", "existing"]
    method: str  # llm | fallback for "created"; how the incident was originally written otherwise

    @property
    def created(self) -> bool:
        return self.action == "created"

    @property
    def event(self) -> EventType | None:
        """The live event to announce, if the incident changed."""
        if self.action == "existing":
            return None
        return "incident.created" if self.created else "incident.updated"


async def load_evidence(session: AsyncSession, anomaly: Anomaly) -> Evidence:
    """Rank the anomaly window's log patterns against the hour before it."""
    start, end = evidence_window(anomaly.window_start, anomaly.window_end)
    same_service = [] if anomaly.service is None else [LogEvent.service == anomaly.service]

    rows = await session.execute(
        select(
            LogEvent.id, LogEvent.timestamp, LogEvent.level, LogEvent.message, LogEvent.template_id
        )
        .where(LogEvent.timestamp >= start, LogEvent.timestamp < end, *same_service)
        .order_by(LogEvent.timestamp)
        .limit(MAX_WINDOW_EVENTS)
    )
    window = [EvidenceEvent(i, ts, lvl, msg, tid or "") for i, ts, lvl, msg, tid in rows]

    # The baseline only needs counts, so let Postgres do the grouping
    baseline_rows = await session.execute(
        select(LogEvent.template_id, func.count())
        .where(
            LogEvent.timestamp >= start - BASELINE_PERIOD,
            LogEvent.timestamp < start,
            *same_service,
        )
        .group_by(LogEvent.template_id)
    )
    baseline = {tid or "": n for tid, n in baseline_rows}

    return build_evidence(anomaly.service or "all services", start, end, window, baseline)


async def _join_open_incident(
    session: AsyncSession, anomaly: Anomaly, gap: timedelta
) -> TriageOutcome | None:
    """Under the service lock: the anomaly's incident if it has or joins one."""
    await lock_service(session, anomaly.service)
    # Re-read under the lock: a racing task may have linked it meanwhile
    linked = await session.scalar(select(Anomaly.incident_id).where(Anomaly.id == anomaly.id))
    if linked is not None:
        return TriageOutcome(linked, "existing", await _method_of(session, linked))
    incident_id = await find_open_incident(session, anomaly, gap)
    if incident_id is None:
        return None
    await attach(session, incident_id, anomaly)
    return TriageOutcome(incident_id, "grouped", await _method_of(session, incident_id))


async def _method_of(session: AsyncSession, incident_id: int) -> str:
    method = await session.scalar(select(Incident.triage_method).where(Incident.id == incident_id))
    return method or "fallback"


async def triage(
    session: AsyncSession,
    anomaly_id: int,
    *,
    embedder: Embedder,
    llm: LLMClient | None,
    group_gap: timedelta = DEFAULT_GROUP_GAP,
) -> TriageOutcome:
    """Attach the anomaly to an open incident, or open a new one.

    Grouping is checked first so a flapping fault costs one LLM call, not one
    per anomaly. Raises ``LookupError`` if the anomaly doesn't exist.
    """
    anomaly = await session.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise LookupError(f"Anomaly {anomaly_id} not found")

    outcome = await _join_open_incident(session, anomaly, group_gap)
    await session.commit()  # releases the service lock
    if outcome is not None:
        log.info("anomaly_grouped", anomaly_id=anomaly_id, incident_id=outcome.incident_id)
        return outcome

    evidence = await load_evidence(session, anomaly)
    runbooks = await runbook_context(session, embedder, evidence.query_text())
    ctx = TriageContext(
        detector=anomaly.detector,
        service=anomaly.service or "all services",
        window_start=anomaly.window_start,
        window_end=anomaly.window_end,
        score=anomaly.score,
        evidence=evidence,
        runbooks=runbooks,
    )
    # End the read transaction: don't hold a connection "idle in transaction"
    # (or the service lock) for the seconds an LLM call can take
    await session.commit()

    report, method = await summarize(ctx, llm)

    # Check again: another worker may have opened an incident for this service
    # while the LLM was running. Then this report is dropped and the anomaly joins.
    outcome = await _join_open_incident(session, anomaly, group_gap)
    if outcome is not None:
        await session.commit()
        log.info("anomaly_grouped_after_race", anomaly_id=anomaly_id)
        return outcome

    evidence_json: list[dict[str, Any]] = [
        {**asdict(t), "lift": round(t.lift, 1)} for t in evidence.templates
    ]
    incident_id = await session.scalar(
        insert(Incident)
        .values(
            anomaly_id=anomaly_id,
            service=anomaly.service,
            started_at=anomaly.window_start,
            last_seen_at=anomaly.window_end,
            summary=report.summary,
            root_cause=report.root_cause,
            confidence=report.confidence,
            runbook=report.runbook,
            next_steps=report.next_steps,
            related_event_ids=report.evidence_event_ids or evidence.event_ids,
            evidence=evidence_json,
            triage_method=method,
            llm_model=llm.model if method == "llm" and llm is not None else None,
        )
        .returning(Incident.id)
    )
    await session.execute(
        update(Anomaly).where(Anomaly.id == anomaly_id).values(incident_id=incident_id)
    )
    await session.commit()
    assert incident_id is not None

    log.info(
        "incident_opened",
        anomaly_id=anomaly_id,
        incident_id=incident_id,
        method=method,
        runbook=report.runbook,
    )
    return TriageOutcome(incident_id, "created", method)

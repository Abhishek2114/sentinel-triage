"""Triage one anomaly: evidence → runbooks → report → incident.

Shared by the Celery task (automatic, for new anomalies) and the
``POST /anomalies/{id}/triage`` endpoint (on demand).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.anomaly import Anomaly
from app.models.event import LogEvent
from app.models.incident import Incident
from app.rag.embeddings import Embedder
from app.rag.retriever import runbook_context
from app.rag.summarizer import LLMClient, TriageContext, summarize
from app.triage.evidence import (
    BASELINE_PERIOD,
    Evidence,
    EvidenceEvent,
    build_evidence,
    evidence_window,
)

log = get_logger(__name__)

# Bounds the work and the prompt size for a very noisy window
MAX_WINDOW_EVENTS = 20_000


@dataclass
class TriageOutcome:
    incident_id: int
    created: bool
    method: str


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


async def triage(
    session: AsyncSession,
    anomaly_id: int,
    *,
    embedder: Embedder,
    llm: LLMClient | None,
) -> TriageOutcome:
    """Create the incident for an anomaly, or return the one that exists.

    Raises ``LookupError`` if the anomaly doesn't exist.
    """
    anomaly = await session.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise LookupError(f"Anomaly {anomaly_id} not found")

    # Checked before the LLM call so a retried task doesn't pay for it twice
    existing = await session.scalar(select(Incident.id).where(Incident.anomaly_id == anomaly_id))
    if existing is not None:
        return TriageOutcome(existing, created=False, method="existing")

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
    # for the seconds an LLM call can take
    await session.commit()

    report, method = await summarize(ctx, llm)

    evidence_json: list[dict[str, Any]] = [
        {**asdict(t), "lift": round(t.lift, 1)} for t in evidence.templates
    ]
    stmt = (
        insert(Incident)
        .values(
            anomaly_id=anomaly_id,
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
        # Two workers can race on the same anomaly; the unique key decides
        .on_conflict_do_nothing(index_elements=[Incident.anomaly_id])
        .returning(Incident.id)
    )
    incident_id = await session.scalar(stmt)
    created = incident_id is not None
    if incident_id is None:
        incident_id = await session.scalar(
            select(Incident.id).where(Incident.anomaly_id == anomaly_id)
        )
    await session.commit()
    assert incident_id is not None  # inserted, or the racing insert committed it

    log.info(
        "anomaly_triaged",
        anomaly_id=anomaly_id,
        incident_id=incident_id,
        created=created,
        method=method,
        runbook=report.runbook,
    )
    return TriageOutcome(incident_id, created=created, method=method)

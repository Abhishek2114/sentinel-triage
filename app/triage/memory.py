"""Incident memory: find resolved incidents whose log patterns match a new one.

When an engineer resolves an incident they can say what fixed it. The next
time similar log patterns show up, triage retrieves those incidents and their
fixes, shows them on the incident page and hands them to the LLM.

Ranking uses only the evidence log patterns (templates) on both sides, so a
match means "we have seen these log lines before", not "someone used similar
words in a note". Resolution notes are shown, never ranked on.
``scripts/eval_memory.py`` measures how often the top match is the same kind
of fault.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.rag.hybrid import HybridIndex

# Only the newest resolved incidents are searched; a bounded scan per triage
MAX_CANDIDATES = 500
# Fused-rank floor: a candidate must rank in both lexical lists, or near
# the top of one, to count as similar
MIN_SCORE = 1.0 / 61 + 1.0 / 70


@dataclass(frozen=True, slots=True)
class PastIncident:
    id: int
    service: str | None
    summary: str
    runbook: str | None
    resolution: str | None
    resolved_at: datetime | None
    score: float


def incident_patterns(evidence: Sequence[dict[str, Any]] | None) -> str:
    """The text memory ranks on: an incident's evidence templates."""
    return "\n".join(str(e.get("template", "")) for e in evidence or [])


def rank_similar(
    query: str, candidates: Sequence[str], limit: int = 3, min_score: float = MIN_SCORE
) -> list[tuple[int, float]]:
    if not query.strip() or not candidates:
        return []
    hits = HybridIndex(candidates).search(query, top_k=limit)
    return [(i, s) for i, s in hits if s >= min_score]


async def similar_incidents(
    session: AsyncSession,
    query: str,
    *,
    exclude_id: int | None = None,
    limit: int = 3,
) -> list[PastIncident]:
    """Resolved incidents most similar to ``query`` (evidence templates), best first.

    Any service counts: the same fault on another service has the same fix.
    """
    stmt = (
        select(Incident)
        .where(Incident.status == "resolved")
        .order_by(Incident.resolved_at.desc().nulls_last(), Incident.id.desc())
        .limit(MAX_CANDIDATES)
    )
    if exclude_id is not None:
        stmt = stmt.where(Incident.id != exclude_id)
    rows = list((await session.scalars(stmt)).all())
    texts = [incident_patterns(r.evidence) for r in rows]
    return [
        PastIncident(
            rows[i].id,
            rows[i].service,
            rows[i].summary,
            rows[i].runbook,
            rows[i].resolution,
            rows[i].resolved_at,
            round(score, 4),
        )
        for i, score in rank_similar(query, texts, limit)
    ]

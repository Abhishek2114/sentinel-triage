"""Pick the log lines that explain an anomaly.

Sending every line from the window to an LLM is expensive and buries the signal
under routine traffic. Instead, lines are grouped by template and each template
is ranked by *lift*: how much more often it appears in the anomaly window than
in the hour before. Templates that are new or suddenly frequent are the
evidence; the steady background is left out.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.ingest.parser import normalise_level
from app.ingest.templates import extract_template

# The error-burst detector flags a window once the 5-minute rolling error count
# crosses its threshold, so the fault began up to this long before window_start
EVIDENCE_LEAD = timedelta(minutes=5)
BASELINE_PERIOD = timedelta(minutes=60)
MIN_LIFT = 2.0

_ERROR_LEVELS = {"ERROR", "CRITICAL"}


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """The fields evidence needs from a stored log event."""

    id: int
    timestamp: datetime
    level: str
    message: str
    template_id: str


@dataclass
class TemplateEvidence:
    """One log template's behaviour during the anomaly."""

    template_id: str
    template: str
    count: int
    baseline_count: int
    errors: int
    lift: float
    example: str
    event_ids: list[int] = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return self.baseline_count == 0


@dataclass
class Evidence:
    """What happened on one service around one anomaly."""

    service: str
    start: datetime
    end: datetime
    total_events: int
    error_events: int
    templates: list[TemplateEvidence]

    @property
    def event_ids(self) -> list[int]:
        return [i for t in self.templates for i in t.event_ids]

    def query_text(self, top: int = 5) -> str:
        """Retrieval query: the most telling templates, in rank order."""
        return "\n".join(t.template for t in self.templates[:top])


def evidence_window(window_start: datetime, window_end: datetime) -> tuple[datetime, datetime]:
    """The period whose logs explain an anomaly."""
    return window_start - EVIDENCE_LEAD, window_end


def build_evidence(
    service: str,
    start: datetime,
    end: datetime,
    window_events: Sequence[EvidenceEvent],
    baseline_counts: Mapping[str, int],
    baseline_minutes: float = BASELINE_PERIOD.total_seconds() / 60,
    max_templates: int = 8,
    ids_per_template: int = 5,
) -> Evidence:
    """Rank the window's templates by lift over the baseline.

    ``baseline_counts`` maps template id → occurrences in the baseline period;
    the caller can count those in SQL rather than loading every line.
    """
    window_minutes = max((end - start).total_seconds() / 60, 1.0)

    grouped: dict[str, TemplateEvidence] = {}
    errors_total = 0
    for e in sorted(window_events, key=lambda ev: ev.timestamp):
        t = grouped.get(e.template_id)
        if t is None:
            t = grouped[e.template_id] = TemplateEvidence(
                template_id=e.template_id,
                template=extract_template(e.message),
                count=0,
                baseline_count=baseline_counts.get(e.template_id, 0),
                errors=0,
                lift=0.0,
                example=e.message,
            )
        t.count += 1
        if normalise_level(e.level) in _ERROR_LEVELS:
            t.errors += 1
            errors_total += 1
        if len(t.event_ids) < ids_per_template:
            t.event_ids.append(e.id)

    for t in grouped.values():
        # +0.5 smoothing so a template unseen in the baseline gets a large but
        # finite lift instead of a division by zero
        rate = (t.count + 0.5) / window_minutes
        base_rate = (t.baseline_count + 0.5) / max(baseline_minutes, 1.0)
        t.lift = rate / base_rate

    ranked = sorted(
        grouped.values(), key=lambda t: (t.lift * math.log1p(t.count), t.count), reverse=True
    )
    unusual = [t for t in ranked if t.lift >= MIN_LIFT]
    # A pure volume spike has no unusual template; show the busiest ones then
    chosen = (unusual or ranked)[:max_templates]

    return Evidence(
        service=service,
        start=start,
        end=end,
        total_events=len(window_events),
        error_events=errors_total,
        templates=chosen,
    )

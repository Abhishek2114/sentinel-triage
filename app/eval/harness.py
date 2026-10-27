"""Run detectors over raw log lines and score them against labels.

Uses the same parse → template → ``detect_events`` path as ingest and the
detection API, and the same evidence + retrieval code as triage, so a change
to any of them shows up in the evaluation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from app.detect.features import EventPoint
from app.detect.pipeline import DEFAULT_DETECTORS, FoundAnomaly, detect_events
from app.eval.metrics import (
    DetectorReport,
    FaultLabel,
    IncidentReport,
    RetrievalReport,
    overlaps,
    score_detector,
    score_incidents,
)
from app.ingest.parser import parse_line
from app.ingest.templates import extract_template, template_id
from app.rag.embeddings import Embedder
from app.rag.ingest_docs import RunbookChunk
from app.rag.retriever import rank_in_memory, rank_runbooks
from app.triage.evidence import BASELINE_PERIOD, EvidenceEvent, build_evidence, evidence_window
from app.triage.grouping import group_anomalies


@dataclass(frozen=True, slots=True)
class _Line:
    service: str
    event: EvidenceEvent


def _parse(lines: Iterable[str]) -> list[_Line]:
    """Parse and template raw lines exactly as ``POST /ingest`` does.

    Position among the parsed lines stands in for the database id.
    """
    parsed_lines: list[_Line] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        tid = template_id(extract_template(parsed.message))
        event = EvidenceEvent(
            len(parsed_lines), parsed.timestamp, parsed.level, parsed.message, tid
        )
        parsed_lines.append(_Line(parsed.service, event))
    return parsed_lines


def _points(parsed: Sequence[_Line]) -> list[EventPoint]:
    return [
        EventPoint(p.event.timestamp, p.service, p.event.level, p.event.template_id) for p in parsed
    ]


def events_from_lines(lines: Iterable[str]) -> list[EventPoint]:
    """Parse and template raw lines exactly as ``POST /ingest`` does."""
    return _points(_parse(lines))


@dataclass
class RunbookIndex:
    """Runbook chunks embedded once and reused for every run."""

    embedder: Embedder
    chunks: list[RunbookChunk]
    vectors: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.vectors = self.embedder.embed([c.text for c in self.chunks])

    def top_runbook(self, query: str) -> str | None:
        hits = rank_in_memory(self.embedder.embed([query])[0], self.chunks, self.vectors)
        docs = rank_runbooks(hits)
        return docs[0] if docs else None


@dataclass
class RunResult:
    detectors: dict[str, DetectorReport]
    retrieval: RetrievalReport | None = None
    # Triaged detector's anomalies as incidents: one per anomaly vs. grouped
    incidents: list[IncidentReport] = field(default_factory=list)


def _score_retrieval(
    parsed: Sequence[_Line],
    anomalies: Sequence[FoundAnomaly],
    faults: Sequence[FaultLabel],
    detector: str,
    index: RunbookIndex,
) -> RetrievalReport:
    """For each fault the triaged detector caught: did triage pick its runbook?

    Runbooks are named after the fault they cover (``<fault_type>.md``). Uses
    the first anomaly that caught the fault, as triage would see it first.
    """
    report = RetrievalReport(detector=detector, embedder=index.embedder.name)
    for fault in faults:
        caught = [a for a in anomalies if a.detector == detector and overlaps(a, fault)]
        if not caught:
            continue
        anomaly = min(caught, key=lambda a: a.window_start)
        start, end = evidence_window(anomaly.window_start, anomaly.window_end)
        same = [p.event for p in parsed if p.service == anomaly.service]
        window = [e for e in same if start <= e.timestamp < end]
        baseline = Counter(
            e.template_id for e in same if start - BASELINE_PERIOD <= e.timestamp < start
        )
        evidence = build_evidence(anomaly.service, start, end, window, baseline)
        report.record(fault.fault_type, index.top_runbook(evidence.query_text()))
    return report


def evaluate_run(
    lines: Sequence[str],
    labels: Sequence[Mapping[str, Any]],
    detectors: Sequence[str] = DEFAULT_DETECTORS,
    window_minutes: int = 1,
    runbooks: RunbookIndex | None = None,
    triage_detector: str = "error_burst",
    group_gap: timedelta = timedelta(minutes=15),
) -> RunResult:
    """Evaluate every detector on one run, plus triage (runbook choice, incident grouping)."""
    parsed = _parse(lines)
    faults = [FaultLabel.from_dict(raw) for raw in labels]
    found = detect_events(_points(parsed), detectors, window_minutes)

    # Longest single-service span, not first-to-last overall: an unstructured
    # line is ingested under "unknown" stamped with the current time, and would
    # otherwise stretch a 6-hour run to years and make false alarms/day look tiny
    spans: dict[str, tuple[datetime, datetime]] = {}
    for p in parsed:
        ts = p.event.timestamp
        lo, hi = spans.get(p.service, (ts, ts))
        spans[p.service] = (min(lo, ts), max(hi, ts))
    hours = max(((hi - lo).total_seconds() / 3600 for lo, hi in spans.values()), default=0.0)

    result = RunResult(
        detectors={
            name: score_detector(name, found.anomalies, faults, hours, window_minutes)
            for name in detectors
        }
    )
    if triage_detector in detectors:
        triaged = [a for a in found.anomalies if a.detector == triage_detector]
        result.incidents = [
            score_incidents("none", [[a] for a in triaged], faults),
            score_incidents(
                f"{int(group_gap.total_seconds() // 60)} min",
                group_anomalies(triaged, group_gap),
                faults,
            ),
        ]
        if runbooks is not None:
            result.retrieval = _score_retrieval(
                parsed, found.anomalies, faults, triage_detector, runbooks
            )
    return result

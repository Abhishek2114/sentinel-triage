"""Run detectors over raw log lines and score them against labels.

Uses the same parse → template → ``detect_events`` path as ingest and the
detection API, so a change to any of them shows up in the evaluation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from app.detect.features import EventPoint
from app.detect.pipeline import DEFAULT_DETECTORS, detect_events
from app.eval.metrics import DetectorReport, FaultLabel, score_detector
from app.ingest.parser import parse_line
from app.ingest.templates import extract_template, template_id


def events_from_lines(lines: Iterable[str]) -> list[EventPoint]:
    """Parse and template raw lines exactly as ``POST /ingest`` does."""
    events: list[EventPoint] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        tid = template_id(extract_template(parsed.message))
        events.append(EventPoint(parsed.timestamp, parsed.service, parsed.level, tid))
    return events


def evaluate_run(
    lines: Sequence[str],
    labels: Sequence[Mapping[str, Any]],
    detectors: Sequence[str] = DEFAULT_DETECTORS,
    window_minutes: int = 1,
) -> dict[str, DetectorReport]:
    """Evaluate every detector on one run of logs with its ground-truth labels."""
    events = events_from_lines(lines)
    faults = [FaultLabel.from_dict(raw) for raw in labels]
    found = detect_events(events, detectors, window_minutes)

    # Longest single-service span, not first-to-last overall: an unstructured
    # line is ingested under "unknown" stamped with the current time, and would
    # otherwise stretch a 6-hour run to years and make false alarms/day look tiny
    spans: dict[str, tuple[datetime, datetime]] = {}
    for e in events:
        lo, hi = spans.get(e.service, (e.timestamp, e.timestamp))
        spans[e.service] = (min(lo, e.timestamp), max(hi, e.timestamp))
    hours = max(((hi - lo).total_seconds() / 3600 for lo, hi in spans.values()), default=0.0)

    return {
        name: score_detector(name, found.anomalies, faults, hours, window_minutes)
        for name in detectors
    }

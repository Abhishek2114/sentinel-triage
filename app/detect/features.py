"""Per-minute feature aggregation from log events.

Features per window:
  - event_count: total number of events
  - error_count: events with level ERROR or CRITICAL
  - unique_templates: number of distinct template IDs
  - error_rate: error_count / event_count
  - new_template_count: templates not seen in previous windows
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.schemas import LogEventRead

FEATURE_NAMES = [
    "event_count",
    "error_count",
    "unique_templates",
    "error_rate",
    "new_template_count",
]


@dataclass
class WindowFeatures:
    """Aggregated metrics for a single time window."""

    window_start: datetime
    window_end: datetime
    event_count: int = 0
    error_count: int = 0
    unique_templates: int = 0
    error_rate: float = 0.0
    new_template_count: int = 0
    service: str | None = None

    def to_array(self) -> np.ndarray:
        return np.array([
            self.event_count,
            self.error_count,
            self.unique_templates,
            self.error_rate,
            self.new_template_count,
        ], dtype=np.float64)


def aggregate_windows(
    events: list[LogEventRead],
    window_minutes: int = 1,
    service: str | None = None,
) -> list[WindowFeatures]:
    """Bucket events into fixed-width time windows and compute features.

    Pass ``service`` to build windows for one service only; otherwise all
    services are pooled together. Runs in O(n log n) – one sort, one pass.
    """
    if service is not None:
        events = [e for e in events if e.service == service]
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e.timestamp)
    window_delta = timedelta(minutes=window_minutes)
    start = sorted_events[0].timestamp.replace(second=0, microsecond=0)
    n_windows = int((sorted_events[-1].timestamp - start) // window_delta) + 1

    # One pass: drop each event straight into its bucket
    buckets: list[list[LogEventRead]] = [[] for _ in range(n_windows)]
    for evt in sorted_events:
        buckets[int((evt.timestamp - start) // window_delta)].append(evt)

    windows: list[WindowFeatures] = []
    seen_templates: set[str] = set()

    for i, bucket_events in enumerate(buckets):
        templates_in_window: set[str] = set()
        error_count = 0

        for evt in bucket_events:
            if evt.level in ("ERROR", "CRITICAL", "FATAL"):
                error_count += 1
            if evt.template_id:
                templates_in_window.add(evt.template_id)

        new_templates = templates_in_window - seen_templates
        seen_templates.update(templates_in_window)

        event_count = len(bucket_events)
        window_start = start + i * window_delta
        windows.append(
            WindowFeatures(
                window_start=window_start,
                window_end=window_start + window_delta,
                event_count=event_count,
                error_count=error_count,
                unique_templates=len(templates_in_window),
                error_rate=error_count / event_count if event_count > 0 else 0.0,
                new_template_count=len(new_templates),
                service=service,
            )
        )

    return windows

"""Unit tests for the detection pipeline's in-memory part."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import pytest

from app.detect.base import AnomalyResult
from app.detect.error_burst import ErrorBurstDetector
from app.detect.features import EventPoint, aggregate_windows
from app.detect.pipeline import detect_service, merge_anomalous_windows
from app.ingest.parser import parse_line
from app.ingest.templates import extract_template, template_id
from scripts.generate_logs import generate_logs

T0 = datetime(2024, 6, 15, tzinfo=UTC)


def _result(minute: int, anomalous: bool, score: float = 1.0) -> AnomalyResult:
    return AnomalyResult(
        window_start=T0 + timedelta(minutes=minute),
        window_end=T0 + timedelta(minutes=minute + 1),
        score=score,
        is_anomaly=anomalous,
    )


class TestMergeAnomalousWindows:
    def test_consecutive_windows_become_one_anomaly(self):
        flags = [False, True, True, True, False, False, False]
        merged = merge_anomalous_windows(
            [_result(i, f, score=i) for i, f in enumerate(flags)]
        )
        assert len(merged) == 1
        assert merged[0].window_start == T0 + timedelta(minutes=1)
        assert merged[0].window_end == T0 + timedelta(minutes=4)
        assert merged[0].score == 3  # peak score of the run

    def test_single_quiet_window_does_not_split_a_fault(self):
        flags = [True, False, True]
        assert len(merge_anomalous_windows([_result(i, f) for i, f in enumerate(flags)])) == 1

    def test_long_gap_splits(self):
        flags = [True, False, False, True]
        assert len(merge_anomalous_windows([_result(i, f) for i, f in enumerate(flags)])) == 2

    def test_nothing_flagged(self):
        assert merge_anomalous_windows([_result(0, False)]) == []


def _synthetic_events(seed: int) -> tuple[dict[str, list[EventPoint]], list[dict[str, str]]]:
    lines, labels = generate_logs(seed=seed)
    by_service: dict[str, list[EventPoint]] = defaultdict(list)
    for line in lines:
        p = parse_line(line)
        assert p is not None
        tid = template_id(extract_template(p.message))
        by_service[p.service].append(EventPoint(p.timestamp, p.service, p.level, tid))
    return by_service, labels


@pytest.mark.parametrize("seed", [2001, 2002, 2003])
def test_error_burst_finds_injected_faults(seed):
    """Quality guard on labelled synthetic data (seeds not used for tuning).

    Held-out average is precision ~0.95 / recall ~0.82; the bounds below leave
    room for run-to-run variation while still catching a broken detector.
    """
    by_service, labels = _synthetic_events(seed)
    faults = [
        (lb["service"], datetime.fromisoformat(lb["window_start"]),
         datetime.fromisoformat(lb["window_end"]))
        for lb in labels
    ]

    found = []
    for svc, events in by_service.items():
        for a in detect_service(aggregate_windows(events, service=svc), ErrorBurstDetector()):
            found.append((svc, a.window_start, a.window_end))

    def overlaps(a, f):
        return a[0] == f[0] and a[1] < f[2] and a[2] > f[1]

    true_positives = [a for a in found if any(overlaps(a, f) for f in faults)]
    detected = [f for f in faults if any(overlaps(a, f) for a in found)]
    assert len(true_positives) / max(len(found), 1) >= 0.75  # precision
    assert len(detected) / len(faults) >= 0.5  # recall

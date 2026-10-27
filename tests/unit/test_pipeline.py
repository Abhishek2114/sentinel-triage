"""Unit tests for the detection pipeline's in-memory part."""

from datetime import UTC, datetime, timedelta

import pytest

from app.detect.base import AnomalyResult
from app.detect.features import EventPoint
from app.detect.pipeline import detect_events, merge_anomalous_windows
from app.eval.harness import evaluate_run
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


def _errors(service: str, minutes: range, per_minute: int) -> list[EventPoint]:
    return [
        EventPoint(T0 + timedelta(minutes=m, seconds=i), service, "ERROR", "t")
        for m in minutes
        for i in range(per_minute)
    ]


def _quiet(service: str, minutes: range) -> list[EventPoint]:
    return [EventPoint(T0 + timedelta(minutes=m), service, "INFO", "t") for m in minutes]


class TestDetectEvents:
    def test_each_service_is_scored_separately(self):
        # A burst on "db" must not be blamed on the quiet "web" service
        events = [*_quiet("web", range(60)), *_quiet("db", range(60))]
        events += _errors("db", range(30, 35), per_minute=10)
        found = detect_events(sorted(events, key=lambda e: e.timestamp), ["error_burst"])

        assert found.services == ["db", "web"]
        assert found.windows_scored == 120
        assert [a.service for a in found.anomalies] == ["db"]
        assert found.anomalies[0].detector == "error_burst"

    def test_unknown_detector_is_rejected(self):
        with pytest.raises(ValueError, match="nope"):
            detect_events([], ["nope"])

    def test_no_events(self):
        found = detect_events([])
        assert found.services == [] and found.anomalies == []


@pytest.mark.parametrize("seed", [2001, 2002, 2003])
def test_error_burst_finds_injected_faults(seed):
    """Quality guard on labelled synthetic data (seeds not used for tuning).

    Held-out average is precision 0.95 / recall 0.82 (``scripts/run_eval.py``);
    the bounds below leave room for run-to-run variation while still catching
    a broken detector.
    """
    lines, labels = generate_logs(seed=seed)
    report = evaluate_run(lines, labels, ["error_burst"])["error_burst"]
    assert report.precision >= 0.75
    assert report.recall >= 0.5

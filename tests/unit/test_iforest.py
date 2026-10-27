"""Unit tests for the Isolation Forest detector."""

from datetime import UTC, datetime, timedelta

from app.detect.features import WindowFeatures
from app.detect.iforest import IForestDetector


def _window(minute: int, errors: int) -> WindowFeatures:
    start = datetime(2024, 6, 15, tzinfo=UTC) + timedelta(minutes=minute)
    return WindowFeatures(
        window_start=start,
        window_end=start + timedelta(minutes=1),
        event_count=20,
        error_count=errors,
        unique_templates=10,
        error_rate=errors / 20,
        new_template_count=0,
    )


def test_error_burst_scores_highest():
    windows = [_window(m, errors=0) for m in range(60)]
    windows[30] = _window(30, errors=15)
    results = IForestDetector(contamination=0.02).detect(windows)

    assert len(results) == 60
    # Higher score = more anomalous, consistently across detectors
    assert max(results, key=lambda r: r.score).window_start == windows[30].window_start
    assert results[30].is_anomaly


def test_empty_input():
    assert IForestDetector().detect([]) == []

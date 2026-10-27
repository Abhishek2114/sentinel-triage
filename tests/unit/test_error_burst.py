"""Unit tests for the error-burst baseline detector."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.detect.error_burst import ErrorBurstDetector, _rolling_sum
from app.detect.features import WindowFeatures


def _windows(errors: list[int], events: int = 20) -> list[WindowFeatures]:
    start = datetime(2024, 6, 15, tzinfo=UTC)
    return [
        WindowFeatures(
            window_start=start + timedelta(minutes=i),
            window_end=start + timedelta(minutes=i + 1),
            event_count=events,
            error_count=e,
            error_rate=e / events,
        )
        for i, e in enumerate(errors)
    ]


def test_rolling_sum():
    assert _rolling_sum(np.array([1.0, 2, 3, 4]), 2).tolist() == [1, 3, 5, 7]


def test_flags_burst_not_background_noise():
    errors = [0, 1, 0, 0, 1, 0] * 10 + [3, 4, 3] + [0] * 20
    results = ErrorBurstDetector(span_windows=3, min_errors=5).detect(_windows(errors))
    flagged = [i for i, r in enumerate(results) if r.is_anomaly]
    assert flagged and min(flagged) >= 60
    assert not any(r.is_anomaly for r in results[:60])


def test_threshold_scales_with_normal_error_level():
    # A noisy service that always has ~10 errors per window isn't "on fire" at 12
    busy = ErrorBurstDetector(span_windows=1, min_errors=5)
    results = busy.detect(_windows([10, 11, 9, 10, 12, 10, 9, 11] * 5, events=200))
    assert busy.threshold > 12
    assert not any(r.is_anomaly for r in results)


def test_higher_score_means_more_errors():
    results = ErrorBurstDetector(span_windows=1).detect(_windows([0, 0, 1, 0, 8]))
    assert max(results, key=lambda r: r.score) is results[-1]

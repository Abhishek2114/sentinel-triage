"""Error-burst detector – the simple statistical baseline.

Flags a window when the number of errors over the last few windows is far
above the service's normal error level. Normal level is learned per service,
so the threshold scales with traffic instead of being a hard-coded count.

On the labelled synthetic faults this rule beats Isolation Forest (see
docs/DESIGN.md), which is exactly why it's kept: any ML detector has to beat it
to earn its place.
"""

from __future__ import annotations

import math

import numpy as np

from app.detect.base import AnomalyResult, Detector
from app.detect.features import FEATURE_NAMES, WindowFeatures

_ERROR_COL = FEATURE_NAMES.index("error_count")


def _rolling_sum(values: np.ndarray, span: int) -> np.ndarray:
    """Sum of each value and the ``span - 1`` values before it."""
    cumsum = np.concatenate([[0.0], np.cumsum(values)])
    idx = np.arange(1, len(values) + 1)
    return np.asarray(cumsum[idx] - cumsum[np.maximum(idx - span, 0)], dtype=np.float64)


class ErrorBurstDetector(Detector):
    """Rolling error count against a Poisson-style threshold.

    threshold = max(min_errors, baseline + z * sqrt(baseline))

    where ``baseline`` is the service's typical rolling error count. Expects
    windows in time order with no gaps (what ``aggregate_windows`` produces).
    """

    name = "error_burst"

    def __init__(
        self,
        span_windows: int = 5,
        z: float = 4.0,
        min_errors: int = 5,
        trim_quantile: float = 0.95,
    ) -> None:
        self.span_windows = span_windows
        self.z = z
        self.min_errors = min_errors
        self.trim_quantile = trim_quantile
        self._baseline: float | None = None

    @property
    def threshold(self) -> float:
        if self._baseline is None:
            raise RuntimeError("Detector must be fitted first.")
        return max(float(self.min_errors), self._baseline + self.z * math.sqrt(self._baseline))

    def fit(self, features: np.ndarray) -> None:
        """Learn the normal rolling error count, ignoring the noisiest windows."""
        rolled = _rolling_sum(features[:, _ERROR_COL], self.span_windows)
        # Trim the top so the faults we're trying to find don't inflate "normal"
        cutoff = np.quantile(rolled, self.trim_quantile)
        self._baseline = float(rolled[rolled <= cutoff].mean())

    def score(self, features: np.ndarray) -> np.ndarray:
        """Standardised excess errors (higher = more anomalous)."""
        if self._baseline is None:
            raise RuntimeError("Detector must be fitted before scoring.")
        rolled = _rolling_sum(features[:, _ERROR_COL], self.span_windows)
        return np.asarray((rolled - self._baseline) / math.sqrt(self._baseline + 1.0))

    def detect(self, windows: list[WindowFeatures]) -> list[AnomalyResult]:
        if not windows:
            return []

        x = np.array([w.to_array() for w in windows])
        if self._baseline is None:
            self.fit(x)

        rolled = _rolling_sum(x[:, _ERROR_COL], self.span_windows)
        scores = self.score(x)
        threshold = self.threshold

        return [
            AnomalyResult(
                window_start=wf.window_start,
                window_end=wf.window_end,
                score=float(scores[i]),
                is_anomaly=bool(rolled[i] >= threshold),
                service=wf.service,
            )
            for i, wf in enumerate(windows)
        ]

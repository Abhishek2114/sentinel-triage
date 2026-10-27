"""Isolation Forest anomaly detector."""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import IsolationForest

from app.detect.base import AnomalyResult, Detector
from app.detect.features import WindowFeatures


class IForestDetector(Detector):
    """Wraps scikit-learn's Isolation Forest for log anomaly detection."""

    name = "iforest"

    def __init__(
        self,
        contamination: float = 0.05,
        n_estimators: int = 200,
        random_state: int = 42,
    ) -> None:
        self._model = IsolationForest(
            contamination=contamination,
            n_estimators=n_estimators,
            random_state=random_state,
        )
        self._fitted = False

    def fit(self, features: np.ndarray) -> None:
        """Fit the isolation forest on historical features."""
        self._model.fit(features)
        self._fitted = True

    def score(self, features: np.ndarray) -> np.ndarray:
        """Return anomaly scores (higher = more anomalous)."""
        if not self._fitted:
            raise RuntimeError("Detector must be fitted before scoring.")
        # sklearn's score_samples is the opposite (lower = more anomalous)
        return np.asarray(-self._model.score_samples(features), dtype=np.float64)

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Return predictions: -1 for anomaly, 1 for normal."""
        if not self._fitted:
            raise RuntimeError("Detector must be fitted before predicting.")
        return np.asarray(self._model.predict(features))

    def detect(self, windows: list[WindowFeatures]) -> list[AnomalyResult]:
        """Run detection on a list of WindowFeatures, returning anomaly results.

        If the detector hasn't been fitted it fits on these same windows, which
        assumes most of them are normal. Prefer fitting on a known-good period.
        """
        if not windows:
            return []

        x = np.array([w.to_array() for w in windows])

        if not self._fitted:
            self.fit(x)

        predictions = self.predict(x)
        scores = self.score(x)

        return [
            AnomalyResult(
                window_start=wf.window_start,
                window_end=wf.window_end,
                score=float(scores[i]),
                is_anomaly=bool(predictions[i] == -1),
                service=wf.service,
            )
            for i, wf in enumerate(windows)
        ]

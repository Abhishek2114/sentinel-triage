"""LSTM Autoencoder anomaly detector (Phase 6 – stub for now)."""

from __future__ import annotations

import numpy as np

from app.detect.base import AnomalyResult, Detector
from app.detect.features import WindowFeatures


class LSTMAutoencoder(Detector):
    """Placeholder for the LSTM autoencoder deep detector.

    Full implementation arrives in Phase 6.
    """

    name = "lstm_ae"

    def fit(self, features: np.ndarray) -> None:
        raise NotImplementedError("LSTM autoencoder is not yet implemented (Phase 6).")

    def score(self, features: np.ndarray) -> np.ndarray:
        raise NotImplementedError("LSTM autoencoder is not yet implemented (Phase 6).")

    def detect(self, windows: list[WindowFeatures]) -> list[AnomalyResult]:
        raise NotImplementedError("LSTM autoencoder is not yet implemented (Phase 6).")

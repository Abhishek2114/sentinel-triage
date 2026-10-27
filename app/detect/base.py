"""Common Detector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.detect.features import WindowFeatures


@dataclass
class AnomalyResult:
    """Output from a detector for a single time window."""

    window_start: datetime
    window_end: datetime
    score: float
    is_anomaly: bool
    service: str | None = None


class Detector(ABC):
    """Base class for all anomaly detectors.

    Every detector uses the same score convention – higher means more
    anomalous – so results from different models can be stored and compared
    side by side in the evaluation.
    """

    name: str = "base"

    @abstractmethod
    def fit(self, features: np.ndarray) -> None:
        """Train/fit the detector on historical (mostly normal) feature data."""
        ...

    @abstractmethod
    def score(self, features: np.ndarray) -> np.ndarray:
        """Return an anomaly score per window (higher = more anomalous)."""
        ...

    @abstractmethod
    def detect(self, windows: list[WindowFeatures]) -> list[AnomalyResult]:
        """Score windows and flag the anomalous ones."""
        ...

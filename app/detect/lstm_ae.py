"""LSTM autoencoder detector – the sequence model.

Learns what a normal stretch of minutes looks like for one service and flags
stretches it can't reconstruct. Unlike Isolation Forest, which judges each
minute on its own, it scores a whole sequence, so one stray error minute is
diluted while a sustained change stands out.

torch is imported on first use, so importing the detector registry stays cheap
for processes that never run this model.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import numpy as np

from app.detect.base import AnomalyResult, Detector
from app.detect.features import FEATURE_NAMES, WindowFeatures

if TYPE_CHECKING:
    import torch

# Counts are heavy-tailed; log1p keeps one busy minute from dominating the scale
_COUNT_COLS = [
    FEATURE_NAMES.index(name)
    for name in ("event_count", "error_count", "unique_templates", "new_template_count")
]
# Consistency constant: MAD × 1.4826 estimates the standard deviation of normal data
_MAD_TO_STD = 1.4826


def _sequences(x: np.ndarray, length: int) -> np.ndarray:
    """One sequence ending at each window; the start is padded with the first window."""
    padded = np.concatenate([np.repeat(x[:1], length - 1, axis=0), x])
    idx = np.arange(len(x))[:, None] + np.arange(length)[None, :]
    return np.asarray(padded[idx], dtype=np.float32)


@contextmanager
def _one_thread() -> Iterator[None]:
    """Run torch on one thread: faster for a model this small, and the results
    don't depend on how many cores the machine has, so the evaluation reproduces."""
    import torch

    before = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        yield
    finally:
        torch.set_num_threads(before)


def _build_model(n_features: int, hidden: int, latent: int) -> torch.nn.Module:
    import torch
    from torch import nn

    class _LSTMAutoencoder(nn.Module):
        """Encode a sequence to one vector, then unroll it back into the sequence."""

        def __init__(self) -> None:
            super().__init__()
            self.encoder = nn.LSTM(n_features, hidden, batch_first=True)
            self.to_latent = nn.Linear(hidden, latent)
            self.decoder = nn.LSTM(latent, hidden, batch_first=True)
            self.output = nn.Linear(hidden, n_features)

        def forward(self, seq: torch.Tensor) -> torch.Tensor:
            _, (h, _) = self.encoder(seq)
            z = self.to_latent(h[-1])
            unrolled = z.unsqueeze(1).repeat(1, seq.shape[1], 1)
            out, _ = self.decoder(unrolled)
            return torch.as_tensor(self.output(out))

    return _LSTMAutoencoder()


class LSTMAutoencoder(Detector):
    """Reconstruction error of the last ``seq_len`` minutes, against a robust threshold.

    threshold = median + z × (1.4826 × MAD) of the fitted series' scores

    Like the other detectors it fits on the windows it is given, assuming most
    are normal. Two things keep the faults it is looking for from being
    learned as normal: after a first training pass the worst-reconstructed
    ``trim`` share of sequences is dropped and the model is trained again
    without them, and the median and MAD barely move when a few windows are
    faults. Settings were chosen on development seeds (docs/DESIGN.md §4.12).
    """

    name = "lstm_ae"

    def __init__(
        self,
        seq_len: int = 10,
        hidden: int = 16,
        latent: int = 4,
        epochs: int = 30,
        batch_size: int = 64,
        learning_rate: float = 1e-2,
        trim: float = 0.1,
        z: float = 7.0,
        random_state: int = 42,
    ) -> None:
        self.seq_len = seq_len
        self.hidden = hidden
        self.latent = latent
        self.epochs = epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.trim = trim
        self.z = z
        self.random_state = random_state
        self._model: Any = None
        self._center: np.ndarray | None = None
        self._scale: np.ndarray | None = None
        self._threshold: float | None = None

    @property
    def threshold(self) -> float:
        if self._threshold is None:
            raise RuntimeError("Detector must be fitted first.")
        return self._threshold

    def _scaled(self, features: np.ndarray) -> np.ndarray:
        assert self._center is not None and self._scale is not None
        x = np.asarray(features, dtype=np.float64).copy()
        x[:, _COUNT_COLS] = np.log1p(x[:, _COUNT_COLS])
        return np.asarray((x - self._center) / self._scale)

    def _train(self, seqs: torch.Tensor) -> torch.nn.Module:
        import torch

        torch.manual_seed(self.random_state)
        model = _build_model(seqs.shape[2], self.hidden, self.latent)
        optimiser = torch.optim.Adam(model.parameters(), lr=self.learning_rate)
        shuffle = torch.Generator().manual_seed(self.random_state)
        model.train()
        for _ in range(self.epochs):
            order = torch.randperm(len(seqs), generator=shuffle)
            for batch in torch.split(order, self.batch_size):
                optimiser.zero_grad()
                loss = torch.mean((model(seqs[batch]) - seqs[batch]) ** 2)
                loss.backward()  # type: ignore[no-untyped-call]
                optimiser.step()
        model.eval()
        return model

    def fit(self, features: np.ndarray) -> None:
        """Train the autoencoder on these windows and set the alarm threshold."""
        import torch

        x = np.asarray(features, dtype=np.float64).copy()
        x[:, _COUNT_COLS] = np.log1p(x[:, _COUNT_COLS])
        self._center = x.mean(axis=0)
        # A feature that never varies (e.g. no errors at all) mustn't divide by zero
        self._scale = np.maximum(x.std(axis=0), 1e-3)

        seqs = torch.from_numpy(_sequences(self._scaled(features), self.seq_len))
        with _one_thread():
            self._model = self._train(seqs)
            if self.trim > 0:
                with torch.no_grad():
                    errors = ((self._model(seqs) - seqs) ** 2).mean(dim=(1, 2))
                self._model = self._train(seqs[errors <= torch.quantile(errors, 1 - self.trim)])

        scores = self.score(features)
        median = float(np.median(scores))
        mad = float(np.median(np.abs(scores - median))) * _MAD_TO_STD
        self._threshold = median + self.z * max(mad, 1e-6)

    def score(self, features: np.ndarray) -> np.ndarray:
        """Mean squared reconstruction error of the sequence ending at each window."""
        if self._model is None:
            raise RuntimeError("Detector must be fitted before scoring.")
        import torch

        seqs = torch.from_numpy(_sequences(self._scaled(features), self.seq_len))
        with _one_thread(), torch.no_grad():
            errors = (self._model(seqs) - seqs) ** 2
        return np.asarray(errors.mean(dim=(1, 2)).numpy(), dtype=np.float64)

    def detect(self, windows: list[WindowFeatures]) -> list[AnomalyResult]:
        """Score windows; fits on these same windows first if not fitted yet."""
        if not windows:
            return []

        x = np.array([w.to_array() for w in windows])
        if self._model is None:
            self.fit(x)

        scores = self.score(x)
        threshold = self.threshold
        return [
            AnomalyResult(
                window_start=wf.window_start,
                window_end=wf.window_end,
                score=float(scores[i]),
                is_anomaly=bool(scores[i] >= threshold),
                service=wf.service,
            )
            for i, wf in enumerate(windows)
        ]

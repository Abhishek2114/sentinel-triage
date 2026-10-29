"""Unit tests for the LSTM autoencoder detector."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.detect.features import EventPoint, WindowFeatures
from app.detect.lstm_ae import LSTMAutoencoder, _sequences
from app.detect.pipeline import detect_events
from app.schemas import DetectRequest

# Small and quick; the defaults are for real series
FAST = {"epochs": 10, "hidden": 8}


def _window(minute: int, errors: int, events: int = 20) -> WindowFeatures:
    start = datetime(2024, 6, 15, tzinfo=UTC) + timedelta(minutes=minute)
    return WindowFeatures(
        window_start=start,
        window_end=start + timedelta(minutes=1),
        event_count=events,
        error_count=errors,
        unique_templates=10,
        error_rate=errors / events,
        new_template_count=0,
    )


def _series(n: int = 120, seed: int = 0) -> list[WindowFeatures]:
    rng = np.random.default_rng(seed)
    return [
        _window(m, errors=int(rng.poisson(0.3)), events=int(rng.poisson(20))) for m in range(n)
    ]


def test_sequences_end_at_each_window_and_pad_the_start():
    x = np.arange(4, dtype=np.float64).reshape(4, 1)
    seqs = _sequences(x, 3)
    assert seqs.shape == (4, 3, 1)
    assert seqs[0, :, 0].tolist() == [0, 0, 0]
    assert seqs[3, :, 0].tolist() == [1, 2, 3]


def test_sustained_burst_is_flagged_and_scores_highest():
    windows = _series()
    for m in range(80, 90):
        windows[m] = _window(m, errors=12)
    results = LSTMAutoencoder(**FAST).detect(windows)

    assert len(results) == len(windows)
    flagged = [i for i, r in enumerate(results) if r.is_anomaly]
    assert flagged and all(80 <= i < 100 for i in flagged)
    # Higher score = more anomalous, like every other detector
    assert 80 <= int(np.argmax([r.score for r in results])) < 100


def test_quiet_series_raises_nothing():
    results = LSTMAutoencoder(**FAST).detect(_series(seed=1))
    assert not any(r.is_anomaly for r in results)


def test_same_input_same_scores():
    windows = _series(seed=2)
    first = [r.score for r in LSTMAutoencoder(**FAST).detect(windows)]
    second = [r.score for r in LSTMAutoencoder(**FAST).detect(windows)]
    assert first == second


def test_leaves_torch_thread_count_as_it_was():
    import torch

    before = torch.get_num_threads()
    LSTMAutoencoder(**FAST).detect(_series(n=40))
    assert torch.get_num_threads() == before


def test_constant_series_does_not_divide_by_zero():
    windows = [_window(m, errors=0) for m in range(30)]
    results = LSTMAutoencoder(**FAST).detect(windows)
    assert all(np.isfinite(r.score) for r in results)


def test_empty_input():
    assert LSTMAutoencoder().detect([]) == []


def test_must_fit_before_scoring():
    with pytest.raises(RuntimeError):
        LSTMAutoencoder().score(np.zeros((5, 5)))
    with pytest.raises(RuntimeError):
        _ = LSTMAutoencoder().threshold


def test_runs_through_the_pipeline_when_named():
    start = datetime(2024, 6, 15, tzinfo=UTC)
    events = [
        EventPoint(
            start + timedelta(minutes=m, seconds=s),
            "svc",
            "ERROR" if 80 <= m < 90 and s < 30 else "INFO",
            f"t{s % 7}",
        )
        for m in range(120)
        for s in range(0, 60, 3)
    ]
    found = detect_events(events, ["lstm_ae"])
    assert {a.detector for a in found.anomalies} == {"lstm_ae"}
    burst = start + timedelta(minutes=80)
    assert any(burst <= a.window_start < burst + timedelta(minutes=20) for a in found.anomalies)


def test_api_runs_it_only_when_asked():
    assert DetectRequest().detectors == ["error_burst", "iforest"]
    assert DetectRequest(detectors=["lstm_ae"]).detectors == ["lstm_ae"]

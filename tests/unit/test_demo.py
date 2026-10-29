"""The demo button's synthetic run."""

from datetime import UTC, datetime

from app.api import routes_demo
from app.detect.features import EventPoint
from app.detect.pipeline import Detections, detect_events

START = datetime(2026, 10, 29, 10, tzinfo=UTC)


def _points(rows):
    return [EventPoint(r["timestamp"], r["service"], r["level"], r["template_id"]) for r in rows]


def test_the_fault_in_a_demo_run_is_caught():
    # One draw in ten is a fault the baseline misses; five draws make a miss ~1 in 100,000
    for _ in range(20):
        prepared, fault = routes_demo._synthetic_run(START)
        found = detect_events(_points(prepared.rows), ["error_burst"])
        assert any(a.service == fault["service"] for a in found.anomalies), fault


def test_demo_services_are_synthetic_and_cover_the_hour():
    prepared, fault = routes_demo._synthetic_run(START)
    assert fault["service"].startswith(routes_demo.PREFIX)
    assert all(str(r["service"]).startswith(routes_demo.PREFIX) for r in prepared.rows)
    assert min(r["timestamp"] for r in prepared.rows) >= START


def test_gives_up_after_a_few_draws(monkeypatch):
    calls = []

    def never_caught(events, detectors):
        calls.append(detectors)
        return Detections()

    monkeypatch.setattr(routes_demo, "detect_events", never_caught)
    prepared, fault = routes_demo._synthetic_run(START)
    assert len(calls) == routes_demo._MAX_DRAWS
    assert prepared.rows and fault["fault_type"]

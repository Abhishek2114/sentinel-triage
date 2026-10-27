"""Unit tests for feature aggregation."""

from datetime import UTC, datetime

from app.detect.features import aggregate_windows
from app.schemas import LogEventRead


def _make_event(minute: int, level: str = "INFO", template_id: str = "t1") -> LogEventRead:
    return LogEventRead(
        id=minute,
        timestamp=datetime(2024, 6, 15, 10, minute, 0, tzinfo=UTC),
        service="test-svc",
        level=level,
        message="test message",
        template_id=template_id,
        raw_line=None,
        created_at=datetime(2024, 6, 15, 10, minute, 0, tzinfo=UTC),
    )


class TestAggregateWindows:
    def test_single_window(self):
        events = [_make_event(0), _make_event(0, "ERROR")]
        windows = aggregate_windows(events, window_minutes=1)
        assert len(windows) == 1
        assert windows[0].event_count == 2
        assert windows[0].error_count == 1
        assert windows[0].error_rate == 0.5

    def test_multiple_windows(self):
        events = [_make_event(0), _make_event(1), _make_event(2)]
        windows = aggregate_windows(events, window_minutes=1)
        assert len(windows) == 3

    def test_empty_events(self):
        assert aggregate_windows([], window_minutes=1) == []

    def test_new_templates_counted(self):
        events = [
            _make_event(0, template_id="a"),
            _make_event(1, template_id="b"),
        ]
        windows = aggregate_windows(events, window_minutes=1)
        assert windows[0].new_template_count == 1  # "a" is new
        assert windows[1].new_template_count == 1  # "b" is new

    def test_gap_minutes_produce_empty_windows(self):
        windows = aggregate_windows([_make_event(0), _make_event(3)], window_minutes=1)
        assert [w.event_count for w in windows] == [1, 0, 0, 1]

    def test_multi_minute_windows(self):
        events = [_make_event(m) for m in range(10)]
        windows = aggregate_windows(events, window_minutes=5)
        assert [w.event_count for w in windows] == [5, 5]

    def test_service_filter_tags_windows(self):
        other = _make_event(0).model_copy(update={"service": "other"})
        windows = aggregate_windows([_make_event(0), other], service="test-svc")
        assert windows[0].event_count == 1
        assert windows[0].service == "test-svc"

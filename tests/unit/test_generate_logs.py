"""Unit tests for the synthetic log generator."""

from scripts.generate_logs import generate_logs


class TestGenerateLogs:
    def test_generates_expected_output(self):
        lines, labels = generate_logs(
            duration_hours=1, events_per_minute=10, fault_count=2, seed=42
        )
        assert len(lines) > 0
        assert len(labels) == 2

    def test_labels_have_required_fields(self):
        _, labels = generate_logs(duration_hours=1, events_per_minute=5, fault_count=1, seed=123)
        for label in labels:
            assert "fault_type" in label
            assert "service" in label
            assert "window_start" in label
            assert "window_end" in label
            assert "duration_minutes" in label

    def test_lines_are_sorted_by_timestamp(self):
        lines, _ = generate_logs(
            duration_hours=1, events_per_minute=10, fault_count=2, seed=42
        )
        # Lines are ISO-8601 format, so lexicographic sort == timestamp sort
        assert lines == sorted(lines)

    def test_deterministic_with_seed(self):
        kwargs = {"duration_hours": 1, "events_per_minute": 10, "fault_count": 2, "seed": 99}
        lines1, labels1 = generate_logs(**kwargs)
        lines2, labels2 = generate_logs(**kwargs)
        assert lines1 == lines2
        assert labels1 == labels2

    def test_fault_windows_never_overlap(self):
        # 1 hour with 8 faults leaves a 6-minute gap; durations used to be up to 15
        _, labels = generate_logs(duration_hours=1, events_per_minute=5, fault_count=8, seed=1)
        windows = sorted((lb["window_start"], lb["window_end"]) for lb in labels)
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
            assert prev_end <= next_start

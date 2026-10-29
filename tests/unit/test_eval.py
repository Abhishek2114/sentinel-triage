"""Unit tests for the evaluation metrics, harness and run_eval CLI."""

import argparse
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.detect.pipeline import FoundAnomaly
from app.eval.harness import evaluate_run, events_from_lines
from app.eval.metrics import DetectorReport, FaultLabel, overlaps, score_detector
from scripts import run_eval
from scripts.generate_logs import generate_logs

T0 = datetime(2024, 6, 15, tzinfo=UTC)


def _at(minute: float) -> datetime:
    return T0 + timedelta(minutes=minute)


def _fault(service: str, start: float, end: float, fault_type: str = "cpu_spike") -> FaultLabel:
    return FaultLabel(fault_type, service, _at(start), _at(end))


def _anomaly(service: str, start: float, end: float, detector: str = "d") -> FoundAnomaly:
    return FoundAnomaly(detector, service, _at(start), _at(end), 1.0)


class TestFaultLabel:
    def test_reads_generator_labels(self):
        _, labels = generate_logs(duration_hours=1, events_per_minute=5, fault_count=1, seed=3)
        fault = FaultLabel.from_dict(labels[0])
        assert fault.service == labels[0]["service"]
        assert fault.end - fault.start == timedelta(minutes=labels[0]["duration_minutes"])
        assert fault.start.tzinfo is not None


class TestOverlaps:
    def test_needs_same_service(self):
        assert overlaps(_anomaly("db", 10, 12), _fault("db", 11, 20))
        assert not overlaps(_anomaly("web", 10, 12), _fault("db", 11, 20))

    def test_touching_intervals_do_not_overlap(self):
        assert not overlaps(_anomaly("db", 5, 10), _fault("db", 10, 20))
        assert not overlaps(_anomaly("db", 20, 25), _fault("db", 10, 20))


class TestScoreDetector:
    def test_counts_precision_recall_and_delay(self):
        faults = [_fault("db", 10, 20, "cpu_spike"), _fault("web", 100, 110, "memory_leak")]
        anomalies = [
            _anomaly("db", 13, 18),  # hit, alert at minute 14 → 4 min late
            _anomaly("db", 11, 12),  # second hit on the same fault, earlier
            _anomaly("db", 50, 51),  # false positive
            _anomaly("web", 10, 15),  # wrong time → false positive
            _anomaly("db", 13, 14, detector="other"),  # another detector, ignored
        ]
        r = score_detector("d", anomalies, faults, hours=24)

        assert (r.anomalies, r.true_positives, r.false_positives) == (4, 2, 2)
        assert (r.faults, r.detected) == (2, 1)
        assert r.precision == 0.5
        assert r.recall == 0.5
        assert r.f1 == 0.5
        # The earliest matching alert counts: window 11–12 closes at minute 12
        assert r.delays_seconds == [120.0]
        assert r.false_alarms_per_day == 2.0
        assert r.by_fault_type == {"cpu_spike": [1, 1], "memory_leak": [0, 1]}

    def test_alert_before_fault_start_counts_as_zero_delay(self):
        r = score_detector("d", [_anomaly("db", 5, 15)], [_fault("db", 10, 20)], hours=1)
        assert r.delays_seconds == [0.0]

    def test_window_size_sets_alert_time(self):
        r = score_detector(
            "d", [_anomaly("db", 10, 15)], [_fault("db", 10, 20)], hours=1, window_minutes=5
        )
        assert r.delays_seconds == [300.0]

    def test_silent_detector_scores_zero_not_undefined(self):
        r = score_detector("d", [], [_fault("db", 10, 20)], hours=1)
        assert (r.precision, r.recall, r.f1) == (0.0, 0.0, 0.0)
        assert r.mttd_seconds is None


class TestDetectorReport:
    def test_add_micro_averages_runs(self):
        a = DetectorReport("d", anomalies=2, true_positives=2, faults=2, detected=1, hours=6)
        a.delays_seconds = [60.0]
        a.by_fault_type = {"cpu_spike": [1, 2]}
        b = DetectorReport("d", anomalies=8, true_positives=4, faults=2, detected=2, hours=6)
        b.delays_seconds = [120.0, 180.0]
        b.by_fault_type = {"cpu_spike": [1, 1], "bad_deploy": [1, 1]}

        total = DetectorReport("d")
        total.add(a)
        total.add(b)

        assert total.precision == 6 / 10  # not the mean of 1.0 and 0.5
        assert total.recall == 3 / 4
        assert total.mttd_seconds == 120.0
        assert total.median_ttd_seconds == 120.0
        assert total.by_fault_type == {"cpu_spike": [2, 3], "bad_deploy": [1, 1]}

    def test_to_dict_is_json_serialisable(self):
        r = score_detector("d", [_anomaly("db", 13, 18)], [_fault("db", 10, 20)], hours=6)
        data = json.loads(json.dumps(r.to_dict()))
        assert data["recall"] == 1.0
        assert data["recall_by_fault_type"] == {"cpu_spike": {"detected": 1, "total": 1}}


class TestHarness:
    def test_blank_lines_are_skipped(self):
        lines, _ = generate_logs(duration_hours=1, events_per_minute=5, fault_count=1, seed=1)
        assert len(events_from_lines([*lines, "", "   "])) == len(lines)

    def test_stray_unstructured_line_does_not_stretch_the_run(self):
        # The parser stamps lines without a timestamp with "now", years after the data
        lines, labels = generate_logs(duration_hours=2, events_per_minute=20, fault_count=1, seed=5)
        report = evaluate_run([*lines, "free text with no timestamp"], labels).detectors[
            "error_burst"
        ]
        assert 1.9 < report.hours < 2.1

    def test_evaluation_is_reproducible(self):
        lines, labels = generate_logs(duration_hours=2, events_per_minute=20, fault_count=2, seed=9)
        first = {k: v.to_dict() for k, v in evaluate_run(lines, labels).detectors.items()}
        second = {k: v.to_dict() for k, v in evaluate_run(lines, labels).detectors.items()}
        assert first == second
        assert set(first) == {"error_burst", "iforest"}
        assert first["error_burst"]["faults"] == 2


class TestRunEvalCli:
    def test_parse_seeds(self):
        assert run_eval.parse_seeds("1000-1002,7, 7") == [7, 1000, 1001, 1002]
        with pytest.raises(argparse.ArgumentTypeError):
            run_eval.parse_seeds("5-1")

    def test_writes_report(self, tmp_path, monkeypatch, capsys):
        out = tmp_path / "result.json"
        monkeypatch.setattr(
            "sys.argv",
            ["run_eval.py", "--seeds", "11", "--hours", "1", "--faults", "1", "--output", str(out)],
        )
        run_eval.main()

        result = json.loads(out.read_text())
        assert result["source"]["generator"]["seeds"] == [11]
        assert [d["detector"] for d in result["detectors"]] == ["error_burst", "iforest", "lstm_ae"]
        assert "| `error_burst` |" in capsys.readouterr().out

    def test_scores_existing_files(self, tmp_path, monkeypatch):
        lines, labels = generate_logs(duration_hours=1, events_per_minute=10, fault_count=1, seed=4)
        logs, label_file, out = tmp_path / "e.log", tmp_path / "l.jsonl", tmp_path / "r.json"
        logs.write_text("\n".join(lines))
        label_file.write_text("\n".join(json.dumps(lb) for lb in labels))
        monkeypatch.setattr(
            "sys.argv",
            ["run_eval.py", "--logs", str(logs), "--labels", str(label_file), "--output", str(out)],
        )
        run_eval.main()

        result = json.loads(out.read_text())
        assert result["source"]["logs"] == str(logs)
        assert result["detectors"][0]["faults"] == 1

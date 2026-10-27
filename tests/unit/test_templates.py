"""Unit tests for template extraction."""

from app.ingest.templates import extract_template, template_id


class TestExtractTemplate:
    def test_strips_ip_and_port(self):
        msg = "Connection to 192.168.1.5:5432 failed"
        tmpl = extract_template(msg)
        assert "<IP>" in tmpl
        assert "<NUM>" in tmpl
        assert "192.168.1.5" not in tmpl

    def test_strips_uuid(self):
        msg = "Request 550e8400-e29b-41d4-a716-446655440000 failed"
        tmpl = extract_template(msg)
        assert "<UUID>" in tmpl
        assert "550e8400" not in tmpl

    def test_strips_numbers(self):
        msg = "Processed 42 items in 350ms"
        tmpl = extract_template(msg)
        assert "42" not in tmpl
        assert "350" not in tmpl
        assert "<NUM>" in tmpl

    def test_strips_file_paths(self):
        msg = "Error reading /var/log/app/server.log"
        tmpl = extract_template(msg)
        assert "<PATH>" in tmpl
        assert "/var/log" not in tmpl

    def test_stable_hash(self):
        tmpl = "Connection to <IP>:<NUM> failed"
        id1 = template_id(tmpl)
        id2 = template_id(tmpl)
        assert id1 == id2
        assert len(id1) == 16

    def test_different_messages_same_template(self):
        msg1 = "Connection to 192.168.1.5:5432 failed"
        msg2 = "Connection to 10.0.0.1:3306 failed"
        assert extract_template(msg1) == extract_template(msg2)

    def test_numbers_with_units_and_ids(self):
        # Each distinct latency / user id used to create its own template
        assert extract_template("GC pause 120ms") == extract_template("GC pause 8000ms")
        assert extract_template("User usr_123 ok") == extract_template("User usr_98765 ok")
        assert extract_template("expected v1.2.3") == extract_template("expected v10.0.1")

    def test_ratio_is_not_a_path(self):
        assert extract_template("pool: 12/50 active") == "pool: <NUM>/<NUM> active"

    def test_timestamp_is_one_placeholder(self):
        assert extract_template("at 2024-06-15T10:30:00Z done") == "at <TS> done"

    def test_synthetic_log_template_count_stays_small(self):
        from app.ingest.parser import parse_line
        from scripts.generate_logs import generate_logs

        lines, _ = generate_logs(duration_hours=1, events_per_minute=20, fault_count=2, seed=7)
        templates = set()
        for line in lines:
            parsed = parse_line(line)
            assert parsed is not None
            templates.add(extract_template(parsed.message))
        # ~40 source templates; before the fix this was in the hundreds
        assert len(templates) < 60

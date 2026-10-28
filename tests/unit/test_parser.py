"""Unit tests for the log parser."""

from datetime import UTC, datetime, timedelta

from app.ingest.parser import parse_line


class TestParseISO:
    """Tests for ISO-8601 formatted log lines."""

    def test_basic_iso_line(self):
        line = "2024-06-15T10:30:00.123Z api-gateway ERROR Connection refused"
        result = parse_line(line)
        assert result is not None
        assert result.service == "api-gateway"
        assert result.level == "ERROR"
        assert result.message == "Connection refused"
        assert result.timestamp.year == 2024
        assert result.timestamp.month == 6

    def test_info_level(self):
        line = "2024-06-15T10:30:00Z user-service INFO Request completed"
        result = parse_line(line)
        assert result is not None
        assert result.level == "INFO"
        assert result.service == "user-service"

    def test_warning_level(self):
        line = "2024-06-15T10:30:00Z order-service WARNING High latency detected"
        result = parse_line(line)
        assert result is not None
        assert result.level == "WARNING"

    def test_with_timezone_offset(self):
        line = "2024-06-15T10:30:00+05:30 payment-service ERROR Timeout"
        result = parse_line(line)
        assert result is not None
        assert result.service == "payment-service"


class TestParseJSON:
    """Tests for JSON formatted log lines."""

    def test_json_line(self):
        line = (
            '{"timestamp": "2024-06-15T10:30:00Z", "service": "api-gw", '
            '"level": "error", "message": "fail"}'
        )
        result = parse_line(line)
        assert result is not None
        assert result.service == "api-gw"
        assert result.level == "ERROR"
        assert result.message == "fail"


class TestParseEdgeCases:
    """Edge cases and fallback behavior."""

    def test_empty_line_returns_none(self):
        assert parse_line("") is None
        assert parse_line("   ") is None

    def test_unparseable_falls_back(self):
        result = parse_line("some random text without a timestamp")
        assert result is not None
        assert result.service == "unknown"
        assert result.message == "some random text without a timestamp"

    def test_naive_timestamp_is_utc(self):
        result = parse_line("2024-06-15T10:30:00 svc INFO no offset")
        assert result is not None
        assert result.timestamp.tzinfo is not None
        assert result.timestamp.utcoffset() == UTC.utcoffset(None)

    def test_fatal_is_normalised(self):
        result = parse_line("2024-06-15T10:30:00Z svc FATAL boom")
        assert result is not None
        assert result.level == "CRITICAL"

    def test_oversized_service_is_cut_not_raised(self):
        # Used to raise ValidationError and fail the whole /ingest request with a 500
        result = parse_line("2024-06-15T10:30:00Z " + "s" * 200 + " INFO hi")
        assert result is not None
        assert result.service == "s" * 64

    def test_oversized_json_level_is_rejected_not_raised(self):
        line = '{"timestamp": "2024-06-15T10:30:00Z", "level": "' + "L" * 30 + '"}'
        assert parse_line(line) is None


class TestParseSyslog:
    def test_level_guess_uses_whole_words(self):
        # "deferred" contains "err" and "alerting" contains "alert"
        result = parse_line("Jan 05 10:00:00 host cron[1]: deferred job alerting done")
        assert result is not None
        assert result.level == "INFO"

    def test_level_guess_finds_keyword(self):
        result = parse_line("Jan 05 10:00:00 host app: error while saving")
        assert result is not None
        assert result.level == "ERROR"

    def test_leap_day_is_parsed(self):
        # Used to fall back to "now": strptime without a year assumes 1900 (not a leap year)
        result = parse_line("Feb 29 10:00:00 host app: hello")
        assert result is not None
        assert (result.timestamp.month, result.timestamp.day) == (2, 29)

    def test_timestamp_is_never_in_the_future(self):
        result = parse_line("Dec 31 23:59:00 host app: hello")
        assert result is not None
        assert result.timestamp <= datetime.now(tz=UTC) + timedelta(days=1)

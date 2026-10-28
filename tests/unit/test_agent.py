"""The log-shipping agent: batching, wrapping and retry behaviour."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent.sentinel_ship import Batcher, Sender, is_continuation, wrap


class FakeIngest(BaseHTTPRequestHandler):
    statuses: list[int] = []
    batches: list[list[str]] = []
    auth: list[str | None] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeIngest.auth.append(self.headers.get("Authorization"))
        status = FakeIngest.statuses.pop(0) if FakeIngest.statuses else 200
        if status == 413 and len(body["lines"]) == 1:
            status = 200
        if status == 200:
            FakeIngest.batches.append(body["lines"])
        self.send_response(status)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server():
    FakeIngest.statuses, FakeIngest.batches, FakeIngest.auth = [], [], []
    httpd = HTTPServer(("127.0.0.1", 0), FakeIngest)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_plain_lines_are_wrapped_with_the_service():
    data = json.loads(wrap("disk almost full", "billing"))
    assert data["service"] == "billing" and data["message"] == "disk almost full"
    assert "level" not in data  # the server guesses it from the message


def test_structured_and_continuation_lines_are_left_alone():
    iso = "2026-01-01T00:00:00Z api ERROR boom"
    assert wrap(iso, "billing") == iso
    assert wrap("\tat com.x.Y(Y.java:1)", "billing") == "\tat com.x.Y(Y.java:1)"
    assert is_continuation("Caused by: java.io.IOException")
    assert not is_continuation("  2026-01-01T00:00:00Z api INFO indented but structured")


def test_batches_are_never_cut_inside_a_trace(server):
    sender = Sender(server, "k")
    batcher = Batcher(sender, max_lines=2, max_wait=60)
    head, tail = "2026-01-01T00:00:00Z a ERROR x", "2026-01-01T00:00:01Z a INFO y"
    lines = [head, "\tat f1", "\tat f2", tail]
    for line in lines:
        batcher.add(line)
    batcher.flush()
    assert FakeIngest.batches == [
        ["2026-01-01T00:00:00Z a ERROR x", "\tat f1", "\tat f2"],
        ["2026-01-01T00:00:01Z a INFO y"],
    ]
    assert FakeIngest.auth == ["Bearer k", "Bearer k"]


def test_retries_5xx_and_splits_on_413(server, monkeypatch):
    monkeypatch.setattr("agent.sentinel_ship.time.sleep", lambda s: None)
    FakeIngest.statuses = [503, 413]
    Sender(server, None).send(["l1", "l2"])
    assert FakeIngest.batches == [["l1"], ["l2"]]
    assert FakeIngest.auth[0] is None

#!/usr/bin/env python3
"""Ship log lines to Sentinel Triage. Python 3.9+, standard library only.

    export SENTINEL_API_KEY=...            # or --api-key-file; never on the command line
    python3 sentinel_ship.py --url https://sentinel.example.com --follow /var/log/app/*.log
    kubectl logs -f deploy/api | python3 sentinel_ship.py --url ... --service api -

- Lines are sent in batches to POST /api/v1/ingest with the key in the
  Authorization header (never in the URL).
- A stack trace stays in one batch: a batch is only cut before a line that
  starts a new event, so the server can join the trace into one event.
- Plain lines with no timestamp/service prefix are wrapped as JSON with
  ``--service`` and the time they were read, so they aren't filed as "unknown".
- Network errors, 429 and 5xx are retried with exponential backoff; the
  buffer is bounded and drops the oldest lines (with a warning) if the server
  stays unreachable. A 413 halves the batch.
- --follow tails files like ``tail -F``: it survives rotation and truncation
  and picks up files that appear later and match the patterns.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime

# Mirrors app/ingest/parser.py and app/ingest/pipeline.py
_STRUCTURED = re.compile(
    r"^(?:\{"
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"|\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s)"
)
_CONTINUATION = re.compile(
    r"^(?:[ \t]+\S|Caused by:|Suppressed:|\.\.\. \d+ more|Traceback \(most recent call last\):"
    r"|During handling of the above exception|The above exception was the direct cause"
    r"|[\w$.]+(?:Exception|Error|Throwable)(?::|$)|goroutine \d+ \[)"
)

MAX_BUFFER_LINES = 50_000


def log(msg: str) -> None:
    print(f"sentinel_ship: {msg}", file=sys.stderr, flush=True)


def is_continuation(line: str) -> bool:
    return bool(_CONTINUATION.match(line)) and not _STRUCTURED.match(line.lstrip())


def wrap(line: str, service: str | None) -> str:
    """Give a plain line a timestamp and service, so it isn't stored as 'unknown'."""
    if service is None or _STRUCTURED.match(line) or is_continuation(line):
        return line
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return json.dumps({"timestamp": now, "service": service, "message": line})


class Sender:
    def __init__(self, url: str, key: str | None, timeout: float = 15.0) -> None:
        self.endpoint = url.rstrip("/") + "/api/v1/ingest"
        self.key = key
        self.timeout = timeout
        self.sent = 0

    def _post(self, lines: list[str]) -> int:
        body = json.dumps({"lines": lines}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "sentinel-ship/1")
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    def send(self, lines: list[str]) -> None:
        """Send until accepted. Gives up only on a 4xx the server won't change its mind on."""
        backoff = 1.0
        pending = [lines]
        while pending:
            batch = pending.pop(0)
            try:
                status = self._post(batch)
            except (urllib.error.URLError, OSError) as exc:
                log(f"server unreachable ({exc}); retrying in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                pending.insert(0, batch)
                continue
            if status < 300:
                self.sent += len(batch)
                backoff = 1.0
            elif status == 413 and len(batch) > 1:
                half = len(batch) // 2
                pending[:0] = [batch[:half], batch[half:]]
            elif status in (429, 502, 503, 504) or status >= 500:
                log(f"HTTP {status}; retrying in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                pending.insert(0, batch)
            elif status == 401:
                log("HTTP 401: set SENTINEL_API_KEY (or --api-key-file) to a valid key")
                sys.exit(2)
            else:
                log(f"HTTP {status}: dropping {len(batch)} lines")


class Batcher:
    """Collect lines; cut batches only where a new event starts."""

    def __init__(self, sender: Sender, max_lines: int, max_wait: float) -> None:
        self.sender = sender
        self.max_lines = max_lines
        self.max_wait = max_wait
        self.buffer: deque[str] = deque()
        self.oldest = 0.0
        self.dropped = 0

    def add(self, line: str) -> None:
        if len(self.buffer) >= self.max_lines and not is_continuation(line):
            self.flush()
        if not self.buffer:
            self.oldest = time.monotonic()
        self.buffer.append(line)
        if len(self.buffer) > MAX_BUFFER_LINES:
            self.buffer.popleft()
            self.dropped += 1
            if self.dropped % 1000 == 1:
                log(f"buffer full; dropped {self.dropped} oldest lines so far")

    def due(self) -> bool:
        return bool(self.buffer) and time.monotonic() - self.oldest >= self.max_wait

    def flush(self) -> None:
        if self.buffer:
            lines = list(self.buffer)
            self.buffer.clear()
            self.sender.send(lines)


def read_stdin() -> Iterator[str | None]:
    """Lines from stdin; None when idle so the caller can flush on time."""
    import selectors

    sel = selectors.DefaultSelector()
    sel.register(sys.stdin, selectors.EVENT_READ)
    while True:
        if sel.select(timeout=0.5):
            line = sys.stdin.readline()
            if not line:
                return
            yield line.rstrip("\n")
        else:
            yield None


class Tail:
    """One followed file. Reopens on rotation (new inode) or truncation."""

    def __init__(self, path: str, from_start: bool) -> None:
        self.path = path
        self.handle = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
        self.inode = os.fstat(self.handle.fileno()).st_ino
        if not from_start:
            self.handle.seek(0, os.SEEK_END)
        self.partial = ""

    def lines(self) -> Iterator[str]:
        while True:
            chunk = self.handle.readline()
            if not chunk:
                break
            if not chunk.endswith("\n"):
                self.partial += chunk  # a writer is mid-line; wait for the rest
                continue
            yield (self.partial + chunk).rstrip("\n")
            self.partial = ""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return
        if st.st_ino != self.inode or st.st_size < self.handle.tell():
            self.handle.close()
            self.handle = open(self.path, encoding="utf-8", errors="replace")  # noqa: SIM115
            self.inode = st.st_ino
            self.partial = ""


def follow(patterns: list[str], from_start: bool) -> Iterator[str | None]:
    tails: dict[str, Tail] = {}
    last_scan = 0.0
    first = True
    while True:
        if time.monotonic() - last_scan > 10:
            for pattern in patterns:
                for path in glob.glob(pattern):
                    if path not in tails and os.path.isfile(path):
                        # Files present at start honour --from-start; new ones are read whole
                        tails[path] = Tail(path, from_start or not first)
                        log(f"following {path}")
            first = False
            last_scan = time.monotonic()
        got = False
        for tail in list(tails.values()):
            for line in tail.lines():
                got = True
                yield line
        if not got:
            yield None
            time.sleep(0.5)


def read_files(patterns: list[str]) -> Iterator[str | None]:
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    yield line.rstrip("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ship log lines to Sentinel Triage")
    parser.add_argument("paths", nargs="+", help="Files or globs, or - for stdin")
    parser.add_argument("--url", required=True, help="Base URL, e.g. https://sentinel.example.com")
    parser.add_argument("--api-key-file", help="File containing the API key")
    parser.add_argument("--service", help="Service name for plain lines with no prefix")
    parser.add_argument("--follow", action="store_true", help="Keep reading as files grow")
    parser.add_argument(
        "--from-start", action="store_true", help="With --follow, read existing content too"
    )
    parser.add_argument("--batch-lines", type=int, default=500)
    parser.add_argument(
        "--max-wait", type=float, default=1.0, help="Seconds before a partial batch is sent"
    )
    args = parser.parse_args()

    parsed = urllib.parse.urlparse(args.url)
    if parsed.query or parsed.username or parsed.password:
        parser.error("--url must not contain a query string or credentials; use SENTINEL_API_KEY")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        log("warning: sending logs over plain HTTP; use https:// outside localhost")
    key = os.environ.get("SENTINEL_API_KEY")
    if args.api_key_file:
        with open(args.api_key_file, encoding="utf-8") as f:
            key = f.read().strip()

    sender = Sender(args.url, key)
    batcher = Batcher(sender, args.batch_lines, args.max_wait)
    if args.paths == ["-"]:
        source = read_stdin()
    elif args.follow:
        source = follow(args.paths, args.from_start)
    else:
        source = read_files(args.paths)

    try:
        for line in source:
            if line is not None and line.strip():
                batcher.add(wrap(line, args.service))
            if batcher.due():
                batcher.flush()
        batcher.flush()
    except KeyboardInterrupt:
        batcher.flush()
    log(f"sent {sender.sent} lines")


if __name__ == "__main__":
    main()

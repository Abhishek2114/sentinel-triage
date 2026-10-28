"""Raw lines → rows ready to insert: the work /ingest does before the database.

Steps, in order:
1. cut over-long lines (one 5 MB line must not become one 5 MB row);
2. join continuation lines (stack traces) onto the event they belong to;
3. parse each event and replace far-future timestamps with the receive time;
4. redact secrets and PII from the message and the raw line;
5. compute the template id from the redacted message.

Pure and synchronous, so the API can run it in a worker thread and keep the
event loop free, and tests can call it directly.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.ingest.parser import is_structured, parse_line
from app.ingest.redact import redact
from app.ingest.templates import extract_template, template_id

TRUNCATED = " …[truncated]"

# Lines that continue the previous event rather than start one: indented
# frames, Java "Caused by"/"... N more", Python traceback headers, and a
# bare exception line such as "java.lang.NullPointerException: x".
_CONTINUATION = re.compile(
    r"^(?:[ \t]+\S"
    r"|Caused by:|Suppressed:|\.\.\. \d+ more"
    r"|Traceback \(most recent call last\):"
    r"|During handling of the above exception"
    r"|The above exception was the direct cause"
    r"|[\w$.]+(?:Exception|Error|Throwable)(?::|$)"
    r"|goroutine \d+ \[)"
)


def is_continuation(line: str) -> bool:
    return bool(_CONTINUATION.match(line)) and not is_structured(line)


def group_multiline(lines: Iterable[str], max_lines: int = 200) -> list[list[str]]:
    """Group raw lines into events: a head line plus its continuation lines.

    A continuation with no event before it (the first line of a batch) is
    kept as its own event. An event stops absorbing lines at ``max_lines``,
    so a runaway trace can't grow one row without bound.
    """
    events: list[list[str]] = []
    for line in lines:
        if not line.strip():
            continue
        if events and len(events[-1]) < max_lines and is_continuation(line):
            events[-1].append(line.rstrip())
        else:
            events.append([line])
    return events


@dataclass
class PreparedBatch:
    rows: list[dict[str, object]] = field(default_factory=list)
    errors: int = 0
    # truncated, multiline_joined, timestamp_clamped, redacted:<kind>
    stats: Counter[str] = field(default_factory=Counter)


def _cut(line: str, max_chars: int, stats: Counter[str]) -> str:
    if len(line) <= max_chars:
        return line
    stats["truncated"] += 1
    return line[:max_chars] + TRUNCATED


def prepare_lines(
    lines: Iterable[str],
    *,
    received_at: datetime | None = None,
    max_line_chars: int = 16_384,
    max_clock_skew: timedelta = timedelta(minutes=5),
    max_multiline_lines: int = 200,
) -> PreparedBatch:
    received_at = received_at or datetime.now(tz=UTC)
    latest_allowed = received_at + max_clock_skew
    out = PreparedBatch()
    cut = (_cut(line, max_line_chars, out.stats) for line in lines)

    for group in group_multiline(cut, max_multiline_lines):
        parsed = parse_line(group[0])
        if parsed is None:
            out.errors += 1
            continue
        if len(group) > 1:
            out.stats["multiline_joined"] += len(group) - 1
            tail = "\n".join(group[1:])
            parsed.message = f"{parsed.message}\n{tail}"
            parsed.raw_line = f"{parsed.raw_line}\n{tail}"
        if parsed.timestamp > latest_allowed:
            # A clock that is wrong (or lying) by more than the skew would
            # otherwise put events in the future: past the scheduled
            # detection window and stretching any span computed from them
            out.stats["timestamp_clamped"] += 1
            parsed.timestamp = received_at

        message = redact(parsed.message)
        raw = redact(parsed.raw_line or "")
        for kind, n in (message.counts | raw.counts).items():
            out.stats[f"redacted:{kind}"] += n
        parsed.message = message.text
        parsed.raw_line = raw.text
        # Template the first line only: frames carry line numbers and
        # addresses, and the first line names the error
        parsed.template_id = template_id(extract_template(message.text.split("\n", 1)[0]))
        out.rows.append(parsed.model_dump())
    return out

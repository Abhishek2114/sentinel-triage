"""Log-line parser – converts raw text lines into LogEvent dicts.

Supports several common log formats:
  1. ISO-8601:  2024-01-15T10:30:00Z service-name ERROR Some message
  2. Syslog:    Jan 15 10:30:00 hostname service[pid]: message
  3. JSON:      {"timestamp": "...", "service": "...", "level": "...", "message": "..."}
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.schemas import LogEventCreate

# ── Regex patterns ────────────────────────────────────

_ISO_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"(?P<service>[\w./-]+)\s+"
    r"(?P<level>DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\s+"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)

_SYSLOG_PATTERN = re.compile(
    r"^(?P<timestamp>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<service>[\w./-]+?)(?:\[\d+\])?:\s+"
    r"(?P<message>.+)$",
)

_LEVEL_KEYWORDS = {
    "fatal": "CRITICAL",
    "emerg": "CRITICAL",
    "alert": "CRITICAL",
    "crit": "CRITICAL",
    "critical": "CRITICAL",
    "err": "ERROR",
    "error": "ERROR",
    "warn": "WARNING",
    "warning": "WARNING",
    "notice": "INFO",
    "info": "INFO",
    "debug": "DEBUG",
}

# Whole words only, so "deferred" isn't an ERROR and "alerting" isn't CRITICAL
_LEVEL_WORD_PATTERN = re.compile(
    r"\b(" + "|".join(_LEVEL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


_SERVICE_BAD_CHARS = re.compile(r"[^A-Za-z0-9._/-]+")
MAX_SERVICE_CHARS = 64


def sanitize_service(raw: str) -> str:
    """A service name safe to store, display and put in a prompt.

    JSON logs can name a service anything, including newlines and markup.
    Names are cut to one token of ``[A-Za-z0-9._/-]``, the same characters the
    ISO and syslog formats accept, so every format yields the same kind of name.
    """
    cleaned = _SERVICE_BAD_CHARS.sub("-", raw.strip()).strip("-./")
    return cleaned[:MAX_SERVICE_CHARS] or "unknown"


def normalise_level(raw: str) -> str:
    """Map variant level names to canonical values."""
    return _LEVEL_KEYWORDS.get(raw.lower(), raw.upper())


def _guess_level_from_message(message: str) -> str:
    """Heuristic: use the first level keyword that appears as a whole word."""
    match = _LEVEL_WORD_PATTERN.search(message)
    return _LEVEL_KEYWORDS[match.group(1).lower()] if match else "INFO"


def _parse_iso_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp; timestamps without an offset are taken as UTC."""
    raw = raw.strip().replace("Z", "+00:00")
    # Handle compact timezone like +0530 -> +05:30
    raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(tz=UTC)
    # Mixing naive and aware datetimes breaks window aggregation downstream
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _parse_syslog_timestamp(raw: str) -> datetime:
    """Parse a year-less syslog timestamp as the most recent matching date.

    Picks the latest year in which the date exists and isn't in the future, so a
    "Dec 31" line read on Jan 1 lands last year and "Feb 29" lands in a leap year.
    """
    now = datetime.now(tz=UTC)
    for year in range(now.year, now.year - 5, -1):
        try:
            ts = datetime.strptime(f"{year} {raw}", "%Y %b %d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        if ts <= now + timedelta(days=1):  # a day of slack for clock skew
            return ts
    return now


def is_structured(line: str) -> bool:
    """Does the line start a new event in one of the known formats?"""
    line = line.strip()
    return (
        line.startswith("{")
        or _ISO_PATTERN.match(line) is not None
        or _SYSLOG_PATTERN.match(line) is not None
    )


def parse_line(line: str) -> LogEventCreate | None:
    """Parse a single log line into a LogEventCreate, or None if unparseable."""
    try:
        return _parse_line(line)
    except ValidationError:
        # e.g. a service name or level longer than the DB column allows
        return None


def _parse_line(line: str) -> LogEventCreate | None:
    line = line.strip()
    if not line:
        return None

    # ── Try JSON ──────────────────────────────────────
    if line.startswith("{"):
        try:
            data = json.loads(line)
            message = str(data.get("message", ""))
            level = data.get("level")
            return LogEventCreate(
                timestamp=_parse_iso_timestamp(str(data.get("timestamp", ""))),
                service=sanitize_service(str(data.get("service", "unknown"))),
                # Shippers that wrap plain lines can't know the level; guess it
                level=normalise_level(str(level)) if level else _guess_level_from_message(message),
                message=message,
                raw_line=line,
            )
        except (json.JSONDecodeError, KeyError):
            pass

    # ── Try ISO-8601 format ───────────────────────────
    m = _ISO_PATTERN.match(line)
    if m:
        return LogEventCreate(
            timestamp=_parse_iso_timestamp(m.group("timestamp")),
            service=sanitize_service(m.group("service")),
            level=normalise_level(m.group("level")),
            message=m.group("message"),
            raw_line=line,
        )

    # ── Try syslog format ────────────────────────────
    m = _SYSLOG_PATTERN.match(line)
    if m:
        return LogEventCreate(
            timestamp=_parse_syslog_timestamp(m.group("timestamp")),
            service=sanitize_service(m.group("service")),
            level=_guess_level_from_message(m.group("message")),
            message=m.group("message"),
            raw_line=line,
        )

    # ── Fallback: store as-is ─────────────────────────
    return LogEventCreate(
        timestamp=datetime.now(tz=UTC),
        service="unknown",
        level=_guess_level_from_message(line),
        message=line,
        raw_line=line,
    )

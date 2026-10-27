"""Drain-style log template extraction.

Strips variable parts (IPs, numbers, UUIDs, paths) from log messages to
produce canonical templates that can be clustered and embedded.

Example:
  Input:  "Connection to 192.168.1.5:5432 failed after 3 retries"
  Output: "Connection to <IP>:<NUM> failed after <NUM> retries"
"""

from __future__ import annotations

import hashlib
import re

# ── Replacement patterns (order matters) ──────────────

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ISO_TS = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"

_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    # UUIDs
    (re.compile(_UUID), "<UUID>"),
    # ISO timestamps (before IP/NUM so their digits aren't split up)
    (re.compile(_ISO_TS), "<TS>"),
    # IPv4 addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    # Hex strings (e.g. commit hashes, session IDs) – 8+ chars with at least one digit
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<HEX>"),
    # File/URL paths – must start a token, so "12/50" is not treated as a path
    (re.compile(r"(?<![\w/])(?:/[\w.-]+)+"), "<PATH>"),
    # Any token containing a digit: plain numbers, but also "350ms", "usr_123",
    # "v1.2.3". Without this every distinct latency or user ID becomes its own
    # template, which drowns the unique/new-template detection features.
    (re.compile(r"\b\w*\d[\w.]*\b"), "<NUM>"),
]


def extract_template(message: str) -> str:
    """Replace variable tokens in a log message with placeholders."""
    template = message
    for pattern, replacement in _REPLACEMENTS:
        template = pattern.sub(replacement, template)
    # Collapse repeated placeholders
    template = re.sub(r"(<\w+>)(\s*\1)+", r"\1", template)
    return template.strip()


def template_id(template: str) -> str:
    """Generate a stable short hash for a template string."""
    return hashlib.sha256(template.encode()).hexdigest()[:16]

"""Remove secrets and personal data from log text before it is stored.

Runs on every line at ingest, so credentials never reach Postgres, the
dashboard or the LLM. The summarizer runs it again on what it sends, which
also covers rows stored before redaction existed.

Each match becomes ``[REDACTED:<kind>]``. The patterns favour precision on
values that are unambiguous (key formats, ``key=value`` pairs with a secret-
sounding key, Luhn-valid card numbers); measured recall and false positives
are in ``scripts/eval_redaction.py``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

_SECRET_KEYS = (
    r"password|passwd|pwd|passphrase|secret|client_secret|api[_-]?key|apikey|access[_-]?key"
    r"|secret[_-]?key|private[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token"
    r"|session[_-]?id|token|credentials?"
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card(m: re.Match[str]) -> str | None:
    digits = re.sub(r"[ -]", "", m.group(0))
    # Card numbers start 2-6; this skips epoch-millisecond timestamps ("17...")
    # and most numeric ids, which pass the Luhn check 1 time in 10 by chance
    if not (13 <= len(digits) <= 19 and digits[0] in "23456"):
        return None
    return "card" if _luhn_ok(digits) else None


@dataclass(frozen=True, slots=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    # Group to replace (0 = whole match); keeps "password=" readable
    group: int = 0
    # Optional check on the match; returns the kind to use, or None to skip
    check: Callable[[re.Match[str]], str | None] | None = None


# Order matters: specific formats first, so a JWT inside "token=..." is
# reported as a JWT and the generic key=value rule finds nothing left.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
            re.DOTALL,
        ),
    ),
    _Rule("jwt", re.compile(r"\beyJ[\w-]{5,}\.eyJ[\w-]{5,}\.[\w-]{5,}")),
    _Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    _Rule("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    _Rule("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    _Rule("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    _Rule("llm_api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    _Rule("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    _Rule("bearer", re.compile(r"(?i)\b(?:bearer|basic)\s+([A-Za-z0-9._~+/-]{12,}=*)"), group=1),
    _Rule("url_password", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^@\s/]+)@"), group=1),
    _Rule(
        "secret",
        re.compile(
            rf"(?i)(?<![\w-])[\"']?(?:{_SECRET_KEYS})[\"']?\s*[=:]\s*"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;&}\]\)]+)"
        ),
        group=1,
    ),
    _Rule(
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    ),
    _Rule("card", re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])"), check=_card),
)

_PLACEHOLDER = re.compile(r"\[REDACTED:\w+\]")


@dataclass
class Redaction:
    text: str
    counts: Counter[str] = field(default_factory=Counter)


def redact(text: str) -> Redaction:
    """Replace every secret or PII match with ``[REDACTED:<kind>]``."""
    counts: Counter[str] = Counter()
    for rule in _RULES:

        def _sub(m: re.Match[str], rule: _Rule = rule) -> str:
            value = m.group(rule.group)
            if _PLACEHOLDER.fullmatch(value.strip("\"'")):
                return m.group(0)  # already redacted by an earlier rule
            kind = rule.check(m) if rule.check else rule.kind
            if kind is None:
                return m.group(0)
            counts[kind] += 1
            start, end = m.span(rule.group)
            whole = m.group(0)
            offset = m.start(0)
            return whole[: start - offset] + f"[REDACTED:{kind}]" + whole[end - offset :]

        text = rule.pattern.sub(_sub, text)
    return Redaction(text, counts)


def redact_text(text: str) -> str:
    return redact(text).text

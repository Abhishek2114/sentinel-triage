#!/usr/bin/env python3
"""Redaction recall and false-positive rate, on SYNTHETIC lines.

Positives: randomly generated secrets of each kind, embedded in several
line shapes (key=value, JSON, header, free text). A positive counts as caught
when the secret value no longer appears anywhere in the redacted line.

Negatives: every line of a 6-hour synthetic run (no secrets) plus a list of
benign lines chosen to look secret-ish (hashes, ids, timestamps, "token
bucket"...). A false positive is any negative line that redaction changes.

The secrets come from the same patterns' formats, so recall here is an upper
bound: real secrets in shapes the patterns don't know are missed.

    python scripts/eval_redaction.py
"""

from __future__ import annotations

import json
import random
import string
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingest.redact import redact  # noqa: E402
from scripts.generate_logs import generate_logs  # noqa: E402

rng = random.Random(7)
ALNUM = string.ascii_letters + string.digits


def rand(n: int, alphabet: str = ALNUM) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def luhn_card() -> str:
    digits = [rng.choice("4")] + [rng.choice(string.digits) for _ in range(14)]
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    digits.append(str((10 - total % 10) % 10))
    s = "".join(digits)
    return rng.choice([s, " ".join(s[i : i + 4] for i in range(0, 16, 4))])


GENERATORS = {
    "aws_access_key": lambda: "AKIA" + rand(16, string.ascii_uppercase + string.digits),
    "password": lambda: rand(rng.randint(8, 20), ALNUM + "!@#$%^*"),
    "bearer_token": lambda: rand(40, ALNUM + "-._"),
    "jwt": lambda: f"eyJ{rand(20)}.eyJ{rand(30)}.{rand(43, ALNUM + '-_')}",
    "github_token": lambda: "ghp_" + rand(36),
    "stripe_key": lambda: "sk_live_" + rand(24),
    "anthropic_key": lambda: "sk-ant-" + rand(48, ALNUM + "-_"),
    "email": lambda: f"{rand(6).lower()}.{rand(5).lower()}@{rand(7).lower()}.com",
    "card": luhn_card,
    "url_password": lambda: rand(12),
}
SHAPES = {
    "password": [
        "db login failed password={s}",
        '{{"user": "app", "password": "{s}"}}',
        "retrying with passwd: {s}",
    ],
    "bearer_token": ["upstream 401 Authorization: Bearer {s}", "auth header bearer {s} rejected"],
    "url_password": ["connecting to postgres://app:{s}@db.internal:5432/main"],
    "email": ["password reset sent to {s}", "user={s} signed in", "bounce from <{s}>"],
    "card": ["charge declined for card {s}", "payment card_number={s} failed"],
}
DEFAULT_SHAPES = ["request failed key={s}", "loaded credential {s} from env", "token={s} expired"]

BENIGN = [
    "token bucket refilled with 5 tokens",
    "rotated session keys for 4 shards",
    "commit 9f2c1ab4e7d3c0ffee deployed to prod",
    "trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7",
    "epoch 1735689600000 checkpoint written",
    "order 4111111111111112 shipped",
    "request id 550e8400-e29b-41d4-a716-446655440000 completed",
    "password reset email queued for user u_8123",
    "secret rotation completed for 3 services",
    "TLS handshake ok, cipher TLS_AES_128_GCM_SHA256",
    "GET /api/v1/users/12345/orders?page=2 200 13ms",
    "Connection to 10.0.4.17:6379 established",
    "sha256:3b4c1e9a0f7d2c8e5a6b1d0f9e8c7b6a5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a pulled",
]
# Shapes the patterns are known not to handle; reported, not hidden.
# (secret value, line)
HARD = [
    ("correct horse battery staple", "login failed, password: correct horse battery staple"),
    ("+1 415 555 0100", "on-call phone +1 415 555 0100"),
    ("Jane Doe", "account locked for Jane Doe"),
    ("9f8e7d6c5b4a39281706f5e4d3c2b1a0", "using secret 9f8e7d6c5b4a39281706f5e4d3c2b1a0"),
    ("hunter2", "PGPASSWORD hunter2 psql -h db"),
    ("203.0.113.9", "login from 203.0.113.9"),  # IPs are kept on purpose (triage needs them)
]


def main() -> None:
    per_kind: dict[str, dict[str, int]] = {}
    for kind, gen in GENERATORS.items():
        caught = total = 0
        for _ in range(100):
            secret = gen()
            shape = rng.choice(SHAPES.get(kind, DEFAULT_SHAPES))
            line = "2026-01-01T00:00:00Z svc ERROR " + shape.format(s=secret)
            total += 1
            caught += secret not in redact(line).text
        per_kind[kind] = {"caught": caught, "total": total}

    lines, _ = generate_logs(duration_hours=6, seed=1000)
    changed_synthetic = sum(redact(line).text != line for line in lines)
    changed_benign = [b for b in BENIGN if redact(b).text != b]
    kinds = Counter()
    for b in changed_benign:
        kinds.update(redact(b).counts)

    caught = sum(v["caught"] for v in per_kind.values())
    total = sum(v["total"] for v in per_kind.values())
    result: dict[str, Any] = {
        "data": "synthetic",
        "recall": round(caught / total, 4),
        "recall_by_kind": {k: round(v["caught"] / v["total"], 3) for k, v in per_kind.items()},
        "false_positive_lines": {
            "synthetic_run_lines": len(lines),
            "synthetic_run_changed": changed_synthetic,
            "tricky_benign_lines": len(BENIGN),
            "tricky_benign_changed": changed_benign,
        },
        "hard_cases": [
            {"line": line, "secret_removed": secret not in redact(line).text}
            for secret, line in HARD
        ],
    }
    print(json.dumps(result, indent=2))
    out = Path("eval/results/redaction.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()

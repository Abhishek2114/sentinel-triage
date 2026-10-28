"""Secrets and PII in log lines are stored verbatim and sent to the LLM."""

import asyncio
from datetime import UTC, datetime, timedelta

from _harness import api, verdict

from app.rag.summarizer import TriageContext, build_prompt
from app.triage.evidence import EvidenceEvent, build_evidence

SECRETS = {
    "aws key": "AKIAIOSFODNN7EXAMPLE",
    "password": "hunter2-prod",
    "bearer": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl",
    "email": "jane.doe@example.com",
    "card": "4111 1111 1111 1111",
}
lines = [
    f"2026-01-01T00:00:00Z auth-service ERROR S3 upload failed key={SECRETS['aws key']}",
    f"2026-01-01T00:00:01Z auth-service ERROR db login failed password={SECRETS['password']}",
    f"2026-01-01T00:00:02Z auth-service ERROR upstream 401 Authorization: Bearer {SECRETS['bearer']}",
    f"2026-01-01T00:00:03Z auth-service ERROR reset mail bounced for {SECRETS['email']}",
    f"2026-01-01T00:00:04Z auth-service ERROR charge declined card {SECRETS['card']}",
]


async def ingest_and_read() -> list[dict]:
    async with api() as c:
        await c.post("/api/v1/ingest", json={"lines": lines})
        return (await c.get("/api/v1/events", params={"limit": 100})).json()


stored = asyncio.run(ingest_and_read())
t0 = datetime(2026, 1, 1, tzinfo=UTC)
ev = build_evidence(
    "auth-service",
    t0,
    t0 + timedelta(minutes=1),
    [
        EvidenceEvent(e["id"], datetime.fromisoformat(e["timestamp"]), e["level"], e["message"], "t")
        for e in stored
    ],
    {},
)
prompt = build_prompt(TriageContext("error_burst", "auth-service", t0, t0, 1.0, ev, []))

leaks = 0
for name, secret in SECRETS.items():
    in_db = any(secret in (e["message"] + (e["raw_line"] or "")) for e in stored)
    in_llm = secret in prompt
    leaks += in_db + in_llm
    print(f"{name:9} stored={in_db!s:5} sent_to_llm={in_llm}")
verdict(leaks > 0, "secrets/PII reach storage and the LLM prompt")

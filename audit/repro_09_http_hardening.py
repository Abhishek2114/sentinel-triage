"""Public-internet basics: auth, keys in URLs, body size, security headers."""

import asyncio
import os
import tracemalloc

# What an operator would set; ignored by code that has no auth
os.environ["API_KEYS"] = '["audit-key-123"]'

from _harness import api, verdict  # noqa: E402


async def main() -> None:
    problems = []
    async with api() as c:
        r = await c.post("/api/v1/ingest", json={"lines": ["2026-01-01T00:00:00Z a INFO hi"]})
        print("anonymous POST /ingest:", r.status_code)
        if r.status_code < 400:
            problems.append("anonymous write to /ingest")
        r = await c.post("/api/v1/runbooks/index")
        print("anonymous POST /runbooks/index:", r.status_code)
        if r.status_code < 400:
            problems.append("anonymous runbook re-index")
        r = await c.patch("/api/v1/incidents/1", json={"status": "resolved"})
        print("anonymous PATCH /incidents/1:", r.status_code, "(404 = reached the handler)")
        if r.status_code in (200, 404):
            problems.append("anonymous incident mutation")
        r = await c.post(
            "/api/v1/ingest?api_key=sk-live-123", json={"lines": ["2026-01-01T00:00:00Z a INFO x"]}
        )
        print("key in query string:", r.status_code, "(accepted, so keys would end up in access logs)")
        if r.status_code < 400:
            problems.append("credential in URL not rejected")

        auth = {"Authorization": "Bearer audit-key-123"}
        big = ["2026-01-01T00:00:00Z bulk INFO " + "x" * 1000] * 40_000  # ~41 MB
        tracemalloc.start()
        r = await c.post("/api/v1/ingest", json={"lines": big}, headers=auth)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        print(f"~41 MB body: HTTP {r.status_code}, peak Python allocation {peak / 1e6:.0f} MB")
        if r.status_code < 400:
            problems.append("no request body limit")
        # A later timestamp, so it is the newest "bulk" event GET /events returns
        one = ["2026-01-02T00:00:00Z bulk INFO " + "y" * 5_000_000]
        r = await c.post("/api/v1/ingest", json={"lines": one}, headers=auth)
        print("single 5 MB log line:", r.status_code, r.json())
        stored = (await c.get("/api/v1/events", params={"service": "bulk"}, headers=auth)).json()
        longest = max((len(e["message"]) for e in stored), default=0)
        print(f"longest stored message: {longest:,} chars")
        if longest > 1_000_000:
            problems.append("no per-line size limit")

        r = await c.get("/api/v1/health")
        wanted = ["content-security-policy", "x-content-type-options", "referrer-policy"]
        missing = [h for h in wanted if h not in r.headers]
        print("missing security headers:", missing)
        if missing:
            problems.append("missing security headers")
    print("\nproblems:", problems)
    verdict(bool(problems), "; ".join(problems) or "hardening in place")


asyncio.run(main())

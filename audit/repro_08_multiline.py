"""Multi-line stack traces become one bogus event per line."""

import asyncio

from _harness import api, verdict

TRACE = """2026-01-01T00:00:00Z order-service ERROR Unhandled exception in checkout
java.lang.NullPointerException: cart was null
\tat com.shop.Checkout.total(Checkout.java:42)
\tat com.shop.Api.handle(Api.java:17)
Caused by: java.io.IOException: stream closed
\t... 12 more
2026-01-01T00:00:05Z order-service INFO request ok""".splitlines()



async def ingest_and_read() -> list[dict]:
    async with api() as c:
        await c.post("/api/v1/ingest", json={"lines": TRACE})
        return (await c.get("/api/v1/events", params={"limit": 100})).json()


stored = asyncio.run(ingest_and_read())
for e in sorted(stored, key=lambda e: e["id"]):
    first = e["message"].splitlines()[0]
    print(f"{e['service']:14} {e['level']:8} lines={len(e['message'].splitlines())}  {first[:45]!r}")
orphans = sum(e["service"] == "unknown" for e in stored)
print(f"\n{len(TRACE)} lines → {len(stored)} stored events; {orphans} under service 'unknown'")
verdict(orphans > 0, "stack-trace continuation lines become separate 'unknown' events")

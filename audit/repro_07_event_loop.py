"""CPU work in async handlers stalls the whole API process.

/ingest parses and templates every line on the event loop, and /detect fits
models on it. While either runs, a concurrent /health waits.
"""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

# Room for the 100k-line request (~9 MB); ignored by code without a body limit
os.environ["MAX_BODY_BYTES"] = str(50 * 1024 * 1024)

from _harness import api, verdict  # noqa: E402

N = 100_000


async def probe(c, stop: asyncio.Event, out: list[float]) -> None:
    # Loop lag, not request latency: the longest gap between two consecutive
    # completed /health calls. A stall while this task sleeps counts too.
    last = time.perf_counter()
    while not stop.is_set():
        await c.get("/api/v1/health")
        await asyncio.sleep(0.02)
        now = time.perf_counter()
        out.append(now - last)
        last = now


async def timed(c, label: str, coro_fn) -> float:
    lat: list[float] = []
    stop = asyncio.Event()
    p = asyncio.create_task(probe(c, stop, lat))
    await asyncio.sleep(0.1)
    t = time.perf_counter()
    r = await coro_fn()
    took = time.perf_counter() - t
    stop.set()
    await p
    print(f"{label}: HTTP {r.status_code} in {took:.2f}s; longest /health gap {max(lat) * 1000:.0f} ms "
          f"over {len(lat)} probes")
    return max(lat), took


async def main() -> None:
    t0 = datetime.now(tz=UTC) - timedelta(hours=6)
    body = [
        f"{(t0 + timedelta(seconds=i * 0.2)).isoformat()} svc-{i % 5} "
        f"{'ERROR' if i % 50 == 0 else 'INFO'} request {i} took {i % 900}ms user=u{i}"
        for i in range(N)
    ]
    async with api() as c:
        worst_ingest, took = await timed(
            c, f"ingest {N} lines", lambda: c.post("/api/v1/ingest", json={"lines": body})
        )
        print(f"ingest throughput: {N / took:,.0f} lines/s (one request, in-process)")
        worst_detect, _ = await timed(c, "detect", lambda: c.post("/api/v1/detect", json={}))

        # Same, for a fleet of 100 services (30 minutes of traffic each)
        fleet = [
            f"{(t0 + timedelta(minutes=m, seconds=s)).isoformat()} svc-{i:03d} INFO ok"
            for i in range(100)
            for m in range(30)
            for s in (0, 30)
        ]
        await c.post("/api/v1/ingest", json={"lines": fleet})
        worst_fleet, _ = await timed(
            c,
            "detect, 100 services",
            lambda: c.post("/api/v1/detect", json={"since": t0.isoformat()}),
        )
    verdict(
        max(worst_ingest, worst_detect, worst_fleet) > 1.0,
        "event loop blocked > 1s by request CPU work",
    )


asyncio.run(main())

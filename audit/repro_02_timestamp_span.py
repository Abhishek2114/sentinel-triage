"""Attacker-chosen timestamps make detection allocate one window per minute of span.

Two events for one service, years apart, produce millions of windows. The parser
accepts any year up to 9999.
"""

import asyncio
import time
import tracemalloc
from datetime import UTC, datetime, timedelta

from _harness import api, verdict

from app.detect.features import EventPoint, aggregate_windows
from app.detect.pipeline import detect_events


async def stored_timestamp() -> datetime:
    """What POST /ingest stores for a line stamped in year 9999."""
    line = (
        '{"timestamp": "9999-12-31T23:59:00Z", "service": "payment-service", '
        '"level": "ERROR", "message": "x"}'
    )
    async with api() as c:
        await c.post("/api/v1/ingest", json={"lines": [line]})
        return datetime.fromisoformat((await c.get("/api/v1/events")).json()[0]["timestamp"])


stored = asyncio.run(stored_timestamp())
print(f"/ingest stored timestamp {stored.isoformat()}")
far = stored

now = datetime(2026, 1, 1, tzinfo=UTC)
per_window_bytes = 0.0
for days in (1, 7, 30):
    events = [
        EventPoint(now, "svc", "INFO", "a"),
        EventPoint(now + timedelta(days=days), "svc", "ERROR", "b"),
    ]
    tracemalloc.start()
    t = time.perf_counter()
    windows = aggregate_windows(events, service="svc")
    secs = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    per_window_bytes = peak / len(windows)
    print(f"span {days:>3}d: {len(windows):>7} windows, peak {peak / 1e6:7.1f} MB, {secs:.2f}s")

t = time.perf_counter()
detect_events(
    [EventPoint(now, "svc", "INFO", "a"), EventPoint(now + timedelta(days=30), "svc", "ERROR", "b")]
)
print(f"full detect_events over a 30-day span from 2 events: {time.perf_counter() - t:.1f}s")

year = [
    EventPoint(now, "svc", "INFO", "a"),
    EventPoint(now + timedelta(days=365), "svc", "ERROR", "b"),
]
tracemalloc.start()
n_year = len(aggregate_windows(year, service="svc"))
peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
print(f"span 365d: {n_year} windows, peak {peak / 1e6:.0f} MB (actually run)")
if n_year > 30 * 1440:
    minutes_to_9999 = (datetime(9999, 12, 31, tzinfo=UTC) - now).total_seconds() / 60
    print(
        f"span to year 9999: {minutes_to_9999:.2e} windows "
        f"≈ {minutes_to_9999 * per_window_bytes / 1e12:.0f} TB (extrapolated, not run)"
    )
future_accepted = stored.year == 9999
verdict(
    future_accepted or n_year > 30 * 1440,
    "far-future timestamps accepted and detection memory grows with the timestamp span",
)

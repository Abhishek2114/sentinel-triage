"""Every distinct service name gets its own detector fit, with no cap.

One request of N lines with N made-up service names costs N model fits per
detection run (and N rows on the dashboard). Time grows linearly, unbounded.
"""

import time
from datetime import UTC, datetime, timedelta

from _harness import verdict

from app.detect.pipeline import detect_events
from app.ingest.parser import parse_line

now = datetime(2026, 1, 1, tzinfo=UTC)
for n in (10, 100, 500):
    events = []
    for i in range(n):
        for m in range(10):
            e = parse_line(
                f'{{"timestamp": "{(now + timedelta(minutes=m)).isoformat()}", '
                f'"service": "junk-{i}", "level": "ERROR", "message": "x"}}'
            )
            assert e is not None
            events.append(e)
    t = time.perf_counter()
    found = detect_events(events)
    secs = time.perf_counter() - t
    print(f"{n:>4} services x 10 events: {len(found.services)} series, detect {secs:6.2f}s")
verdict(len(found.services) == 500, "service-name cardinality is unbounded (linear detector cost)")

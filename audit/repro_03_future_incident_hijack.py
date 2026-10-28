"""A future-dated error burst opens an incident that silently absorbs real faults.

Grouping joins an anomaly to the service's open incident if it falls within
15 minutes of that incident's span. A burst stamped a day ahead stretches the
span into the future, so the next real fault "joins" it (200, no new incident,
no page) instead of opening a new one (201).
"""

import asyncio
from datetime import UTC, datetime, timedelta

from _harness import api, verdict

SVC = "payment-service"


def lines(start: datetime, minutes: int, errors_from: int | None = None) -> list[str]:
    out = []
    for m in range(minutes):
        ts = start + timedelta(minutes=m)
        for s in range(0, 60, 10):
            level = "ERROR" if errors_from is not None and m >= errors_from else "INFO"
            msg = "db connection refused" if level == "ERROR" else "request ok"
            out.append(f"{(ts + timedelta(seconds=s)).isoformat()} {SVC} {level} {msg}")
    return out


async def main() -> None:
    now = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    async with api() as c:
        # Attacker: normal-looking traffic then a burst, all stamped tomorrow
        fake = lines(now + timedelta(days=1) - timedelta(minutes=30), 30, errors_from=25)
        r = await c.post("/api/v1/ingest", json={"lines": fake})
        print("ingest future-dated:", r.status_code, r.json())
        await c.post("/api/v1/runbooks/index")
        r = await c.post("/api/v1/detect", json={"detectors": ["error_burst"]})
        a = r.json()["anomalies"]
        print("anomalies after attacker burst:", [(x["id"], x["window_start"]) for x in a])
        if not a:
            verdict(False, "the future-dated burst produced no anomaly to hijack with")
        r = await c.post(f"/api/v1/anomalies/{a[0]['id']}/triage")
        inc = r.json()
        print(f"attacker incident #{inc['id']} last_seen_at={inc['last_seen_at']}")

        # Real fault, now
        real = lines(now - timedelta(minutes=60), 60, errors_from=50)
        await c.post("/api/v1/ingest", json={"lines": real})
        r = await c.post(
            "/api/v1/detect",
            json={"detectors": ["error_burst"], "since": (now - timedelta(hours=2)).isoformat()},
        )
        new = [x for x in r.json()["anomalies"] if x["id"] != a[0]["id"]]
        print("real anomalies:", [(x["id"], x["window_start"]) for x in new])
        if not new:
            verdict(False, "no real anomaly detected; scenario did not apply")
        r = await c.post(f"/api/v1/anomalies/{new[0]['id']}/triage")
        print(f"triage of the real fault: HTTP {r.status_code}, incident #{r.json()['id']}")
        verdict(
            r.status_code == 200 and r.json()["id"] == inc["id"],
            "real fault absorbed into a future-dated attacker incident (no new page)",
        )


asyncio.run(main())

"""End-to-end detection against a real Postgres: ingest → detect → anomalies."""

from datetime import datetime, timedelta

import pytest

from scripts.generate_logs import generate_logs

pytestmark = pytest.mark.usefixtures("clean_db")


async def _ingest_synthetic(client, seed: int = 2001) -> list[dict[str, str]]:
    lines, labels = generate_logs(duration_hours=6, seed=seed)
    resp = await client.post("/api/v1/ingest", json={"lines": lines})
    assert resp.status_code == 200
    assert resp.json() == {"ingested": len(lines), "errors": 0}
    return labels


def _overlaps(anomaly: dict, label: dict) -> bool:
    return (
        anomaly["service"] == label["service"]
        and datetime.fromisoformat(anomaly["window_start"])
        < datetime.fromisoformat(label["window_end"])
        and datetime.fromisoformat(anomaly["window_end"])
        > datetime.fromisoformat(label["window_start"])
    )


async def test_detect_finds_injected_faults(client):
    labels = await _ingest_synthetic(client)

    resp = await client.post("/api/v1/detect", json={"detectors": ["error_burst"]})
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["services"]) == 5
    assert body["windows_scored"] > 5 * 350
    assert body["new_anomalies"] == len(body["anomalies"]) > 0
    detected = [lb for lb in labels if any(_overlaps(a, lb) for a in body["anomalies"])]
    assert len(detected) >= len(labels) // 2


async def test_detect_is_idempotent(client):
    await _ingest_synthetic(client)

    first = (await client.post("/api/v1/detect", json={})).json()
    second = (await client.post("/api/v1/detect", json={})).json()

    assert {a["detector"] for a in first["anomalies"]} == {"error_burst", "iforest"}
    assert second["new_anomalies"] == 0
    assert [a["id"] for a in second["anomalies"]] == [a["id"] for a in first["anomalies"]]

    listed = (await client.get("/api/v1/anomalies", params={"limit": 1000})).json()
    assert len(listed) == len(first["anomalies"])


async def test_anomalies_filter_by_service_and_detector(client):
    await _ingest_synthetic(client)
    await client.post("/api/v1/detect", json={})

    svc = "payment-service"
    resp = await client.get("/api/v1/anomalies", params={"service": svc, "detector": "iforest"})
    assert resp.status_code == 200
    assert all(a["service"] == svc and a["detector"] == "iforest" for a in resp.json())


async def test_detect_on_empty_database(client):
    resp = await client.post("/api/v1/detect", json={})
    assert resp.status_code == 200
    assert resp.json() == {"services": [], "windows_scored": 0, "new_anomalies": 0, "anomalies": []}


async def test_unknown_detector_is_rejected(client):
    resp = await client.post("/api/v1/detect", json={"detectors": ["magic"]})
    assert resp.status_code == 422


async def test_rerun_on_shorter_range_does_not_shrink_anomalies(client):
    await _ingest_synthetic(client)
    full = (await client.post("/api/v1/detect", json={"detectors": ["error_burst"]})).json()
    target = full["anomalies"][0]

    # Re-run over a range that ends one minute into that anomaly
    cut = datetime.fromisoformat(target["window_start"]).replace(second=0) + timedelta(minutes=1)
    await client.post(
        "/api/v1/detect", json={"detectors": ["error_burst"], "until": cut.isoformat()}
    )

    after = (await client.get("/api/v1/anomalies", params={"limit": 1000})).json()
    match = next(a for a in after if a["id"] == target["id"])
    assert match["window_end"] == target["window_end"]
    assert match["score"] >= target["score"]  # peak score is kept, never lowered

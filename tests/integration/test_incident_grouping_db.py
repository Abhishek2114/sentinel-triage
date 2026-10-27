"""Incident grouping, status changes and live events against Postgres."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api.deps import get_embedder_dep, get_llm_dep
from app.core.db import async_session_factory
from app.main import app
from app.models.anomaly import Anomaly
from app.models.incident import Incident
from app.rag.embeddings import HashingEmbedder
from app.triage.service import triage

pytestmark = pytest.mark.usefixtures("clean_db")

T0 = datetime(2024, 6, 15, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_llm() -> Iterator[None]:
    app.dependency_overrides[get_embedder_dep] = HashingEmbedder
    app.dependency_overrides[get_llm_dep] = lambda: None
    yield
    app.dependency_overrides.pop(get_embedder_dep, None)
    app.dependency_overrides.pop(get_llm_dep, None)


async def _anomaly(service: str, start_minute: int, minutes: int = 3) -> int:
    async with async_session_factory() as session:
        anomaly = Anomaly(
            detector="error_burst",
            score=5.0,
            service=service,
            window_start=T0 + timedelta(minutes=start_minute),
            window_end=T0 + timedelta(minutes=start_minute + minutes),
        )
        session.add(anomaly)
        await session.commit()
        return anomaly.id


async def test_flapping_fault_is_one_incident(client, published):
    first, second = await _anomaly("db", 0), await _anomaly("db", 8)

    opened = await client.post(f"/api/v1/anomalies/{first}/triage")
    joined = await client.post(f"/api/v1/anomalies/{second}/triage")

    assert opened.status_code == 201
    assert joined.status_code == 200
    incident = joined.json()
    assert incident["id"] == opened.json()["id"]
    assert incident["anomaly_count"] == 2
    assert incident["started_at"] == "2024-06-15T01:00:00Z"
    assert incident["last_seen_at"] == "2024-06-15T01:11:00Z"

    detail = (await client.get(f"/api/v1/incidents/{incident['id']}")).json()
    assert [a["id"] for a in detail["anomalies"]] == [first, second]
    assert all(a["incident_id"] == incident["id"] for a in detail["anomalies"])

    assert [e["type"] for e in published.events] == ["incident.created", "incident.updated"]
    assert published.events[1]["incident"]["anomaly_count"] == 2


async def test_other_service_or_far_away_opens_new_incidents(client):
    ids = [await _anomaly("db", 0), await _anomaly("web", 2), await _anomaly("db", 60)]
    codes = [(await client.post(f"/api/v1/anomalies/{i}/triage")).status_code for i in ids]
    assert codes == [201, 201, 201]


async def test_resolved_incident_does_not_absorb_new_anomalies(client, published):
    first, second = await _anomaly("db", 0), await _anomaly("db", 5)
    incident_id = (await client.post(f"/api/v1/anomalies/{first}/triage")).json()["id"]

    resolved = await client.patch(f"/api/v1/incidents/{incident_id}", json={"status": "resolved"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert published.events[-1]["type"] == "incident.updated"
    assert published.events[-1]["incident"]["status"] == "resolved"

    reopened_elsewhere = await client.post(f"/api/v1/anomalies/{second}/triage")
    assert reopened_elsewhere.status_code == 201
    assert reopened_elsewhere.json()["id"] != incident_id

    open_db = await client.get("/api/v1/incidents", params={"status": "open", "service": "db"})
    assert [i["id"] for i in open_db.json()] == [reopened_elsewhere.json()["id"]]


async def test_update_incident_validation(client, published):
    incident_id = (await client.post(f"/api/v1/anomalies/{await _anomaly('db', 0)}/triage")).json()[
        "id"
    ]
    events_before = len(published.events)

    assert (
        await client.patch("/api/v1/incidents/999", json={"status": "resolved"})
    ).status_code == 404
    assert (
        await client.patch(f"/api/v1/incidents/{incident_id}", json={"status": "closed"})
    ).status_code == 422
    # Already open: no change, no event
    same = await client.patch(f"/api/v1/incidents/{incident_id}", json={"status": "open"})
    assert same.status_code == 200
    assert len(published.events) == events_before


class SlowLLM:
    """Holds both triages inside the LLM call at once, to force the race."""

    model = "slow"

    async def complete_json(self, system: str, user: str) -> str:
        await asyncio.sleep(0.3)
        return "not json"  # → fallback report; the race is what's being tested


async def _triage_in_own_session(anomaly_id: int):
    async with async_session_factory() as session:
        return await triage(session, anomaly_id, embedder=HashingEmbedder(), llm=SlowLLM())


async def test_concurrent_triage_on_one_service_opens_one_incident():
    first, second = await _anomaly("db", 0), await _anomaly("db", 4)

    outcomes = await asyncio.gather(_triage_in_own_session(first), _triage_in_own_session(second))

    assert sorted(o.action for o in outcomes) == ["created", "grouped"]
    assert outcomes[0].incident_id == outcomes[1].incident_id
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        count = await session.scalar(select(Incident.anomaly_count))
    assert count == 2


async def test_concurrent_triage_of_the_same_anomaly_is_counted_once():
    anomaly_id = await _anomaly("db", 0)

    outcomes = await asyncio.gather(
        _triage_in_own_session(anomaly_id), _triage_in_own_session(anomaly_id)
    )

    assert sorted(o.action for o in outcomes) == ["created", "existing"]
    async with async_session_factory() as session:
        assert await session.scalar(select(Incident.anomaly_count)) == 1

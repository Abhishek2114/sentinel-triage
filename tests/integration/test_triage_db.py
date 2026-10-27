"""End-to-end triage against Postgres + pgvector: anomaly → evidence → runbook → incident."""

import json
from collections.abc import Iterator

import pytest

from app.api.routes_triage import get_embedder_dep, get_llm_dep
from app.core.db import async_session_factory
from app.detect.pipeline import run_detection
from app.main import app
from app.models.runbook import RunbookChunkRow
from app.rag.embeddings import HashingEmbedder
from app.rag.index import count_chunks, index_runbooks
from scripts.generate_logs import generate_logs

pytestmark = pytest.mark.usefixtures("clean_db")


class FakeLLM:
    model = "fake-llm-1"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def complete_json(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


@pytest.fixture
def no_llm() -> Iterator[None]:
    app.dependency_overrides[get_embedder_dep] = HashingEmbedder
    app.dependency_overrides[get_llm_dep] = lambda: None
    yield
    app.dependency_overrides.clear()


async def _first_anomaly(client, seed: int = 1003) -> dict:
    """Ingest a synthetic run, detect with error_burst, return the first anomaly."""
    lines, _ = generate_logs(duration_hours=6, seed=seed)
    assert (await client.post("/api/v1/ingest", json={"lines": lines})).status_code == 200
    resp = await client.post("/api/v1/detect", json={"detectors": ["error_burst"]})
    anomalies = resp.json()["anomalies"]
    assert anomalies
    return anomalies[0]


async def test_indexing_replaces_only_this_embedders_chunks(client):
    async with async_session_factory() as session:
        session.add(
            RunbookChunkRow(
                source_doc="old.md",
                chunk_index=0,
                section="s",
                text="t",
                embedding_model="other-model",
                embedding=[0.0] * 384,
            )
        )
        await session.commit()

        assert await index_runbooks(session, HashingEmbedder(), "data/runbooks") == 16
        assert await index_runbooks(session, HashingEmbedder(), "data/runbooks") == 16
        assert await count_chunks(session, "hashing") == 16
        assert await count_chunks(session, "other-model") == 1


@pytest.mark.usefixtures("no_llm")
async def test_triage_creates_one_incident_per_anomaly(client):
    resp = await client.post("/api/v1/runbooks/index")
    assert resp.json() == {"chunks": 16, "embedding_model": "hashing"}
    anomaly = await _first_anomaly(client)

    resp = await client.post(f"/api/v1/anomalies/{anomaly['id']}/triage")
    assert resp.status_code == 201
    incident = resp.json()
    assert incident["anomaly_id"] == anomaly["id"]
    assert incident["triage_method"] == "fallback" and incident["llm_model"] is None
    assert incident["runbook"] == "cpu_spike.md"  # seed 1003's first caught fault
    assert incident["next_steps"]
    assert incident["evidence"][0]["lift"] > 10
    assert incident["related_event_ids"]

    # Same anomaly again: the existing incident, no new work
    again = await client.post(f"/api/v1/anomalies/{anomaly['id']}/triage")
    assert again.status_code == 200
    assert again.json()["id"] == incident["id"]

    detail = (await client.get(f"/api/v1/incidents/{incident['id']}")).json()
    assert detail["anomaly"]["id"] == anomaly["id"]
    assert {e["id"] for e in detail["related_events"]} == set(incident["related_event_ids"])
    assert all(e["service"] == anomaly["service"] for e in detail["related_events"])

    listed = (await client.get("/api/v1/incidents")).json()
    assert [i["id"] for i in listed] == [incident["id"]]


@pytest.mark.usefixtures("no_llm")
async def test_triage_unknown_anomaly_is_404(client):
    resp = await client.post("/api/v1/anomalies/12345/triage")
    assert resp.status_code == 404


@pytest.mark.usefixtures("no_llm")
async def test_triage_without_runbooks_still_creates_incident(client):
    anomaly = await _first_anomaly(client)
    incident = (await client.post(f"/api/v1/anomalies/{anomaly['id']}/triage")).json()
    assert incident["runbook"] is None
    assert incident["root_cause"].startswith("Unknown")


async def test_llm_report_is_grounded_before_saving(client):
    app.dependency_overrides[get_embedder_dep] = HashingEmbedder
    try:
        await client.post("/api/v1/runbooks/index")
        anomaly = await _first_anomaly(client)
        reply = json.dumps(
            {
                "summary": "Thread pool exhausted on the service.",
                "root_cause": "CPU saturation.",
                "confidence": "medium",
                "runbook": "cpu_spike.md",
                "next_steps": ["Scale out"],
                "evidence_event_ids": [987654321],  # not something we sent
            }
        )
        llm = FakeLLM(reply)
        app.dependency_overrides[get_llm_dep] = lambda: llm

        incident = (await client.post(f"/api/v1/anomalies/{anomaly['id']}/triage")).json()
    finally:
        app.dependency_overrides.clear()

    assert llm.calls == 1
    assert incident["triage_method"] == "llm"
    assert incident["llm_model"] == "fake-llm-1"
    assert incident["summary"] == "Thread pool exhausted on the service."
    assert incident["runbook"] == "cpu_spike.md"
    # The made-up id was dropped; the evidence ids are stored instead
    assert 987654321 not in incident["related_event_ids"]
    assert incident["related_event_ids"]


async def test_detection_reports_new_ids_by_detector(client):
    lines, _ = generate_logs(duration_hours=6, seed=1003)
    await client.post("/api/v1/ingest", json={"lines": lines})
    async with async_session_factory() as session:
        summary = await run_detection(session)
        again = await run_detection(session)

    assert set(summary.new_ids_by_detector) == {"error_burst", "iforest"}
    assert sorted(i for ids in summary.new_ids_by_detector.values() for i in ids) == sorted(
        summary.new_anomaly_ids
    )
    assert again.new_ids_by_detector == {}  # nothing new to triage on a re-run

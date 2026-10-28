"""The in-process scheduler used by the single-container deploy."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import scheduler
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.models.incident import Incident
from app.rag.embeddings import HashingEmbedder
from app.rag.index import index_runbooks
from scripts.generate_logs import generate_logs


@pytest.fixture
def offline() -> Iterator[None]:
    """Hashing embedder and no LLM, so nothing is downloaded or called."""
    s = get_settings()
    saved = s.model_dump()
    s.embedding_model = "hashing"
    s.llm_provider = "openai"
    s.openai_api_key = ""
    s.llm_base_url = None
    scheduler._last_ingest = None
    yield
    for k, v in saved.items():
        setattr(s, k, v)
    scheduler._last_ingest = None


async def _incidents() -> int:
    async with async_session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Incident)) or 0


@pytest.mark.usefixtures("clean_db", "offline")
async def test_idle_ticks_touch_nothing(published):
    assert await scheduler.run_once(lambda: published) is False


@pytest.mark.usefixtures("clean_db", "offline")
async def test_ingest_then_tick_opens_and_announces_an_incident(client, published):
    async with async_session_factory() as session:
        await index_runbooks(session, HashingEmbedder(), get_settings().runbooks_dir)

    now = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    lines, labels = generate_logs(
        duration_hours=1, fault_count=1, seed=11, start_time=now - timedelta(minutes=70)
    )
    r = await client.post("/api/v1/ingest", json={"lines": lines})
    assert r.status_code == 200 and r.json()["ingested"] > 0
    assert scheduler._last_ingest is not None  # /ingest told the scheduler

    assert await scheduler.run_once(lambda: published, now=now + timedelta(minutes=1)) is True
    assert await _incidents() >= 1
    assert any(e["type"] == "incident.created" for e in published.events)
    # Everything ingested is now scanned, so the next tick does nothing
    assert scheduler._last_ingest is None
    assert await scheduler.run_once(lambda: published) is False


@pytest.mark.usefixtures("offline")
async def test_logs_in_the_open_minute_get_another_pass(published, monkeypatch):
    async def fake_detect(*args, **kwargs):
        return 0

    monkeypatch.setattr(scheduler, "detect_and_triage", fake_detect)
    now = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)
    scheduler.note_ingest(now)  # inside the minute that isn't complete yet
    assert await scheduler.run_once(lambda: published, now=now) is True
    assert scheduler._last_ingest == now  # still pending
    assert await scheduler.run_once(lambda: published, now=now + timedelta(minutes=1)) is True
    assert scheduler._last_ingest is None

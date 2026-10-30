"""Regression tests for the HTTP-level audit findings and the spend guard."""

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from app.api.deps import get_embedder_dep, get_llm_dep
from app.core.config import get_settings
from app.core.db import async_session_factory, engine
from app.main import app
from app.rag.budget import BudgetedLLM, BudgetExceededError, reserve
from app.rag.embeddings import HashingEmbedder

KEY = "test-key-0123456789"
LINE = "2026-01-01T00:00:00Z svc INFO hello"


@pytest.fixture
def settings() -> Iterator[object]:
    s = get_settings()
    saved = s.model_dump()
    yield s
    for k, v in saved.items():
        setattr(s, k, v)


@pytest.fixture
def keys(settings):
    settings.api_keys = [KEY]
    return settings


class TestAuth:  # audit/repro_09
    async def test_sign_in_check_accepts_only_a_valid_key(self, client, keys):
        assert (await client.get("/api/v1/auth/check")).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert (await client.get("/api/v1/auth/check", headers=wrong)).status_code == 401
        right = {"Authorization": f"Bearer {KEY}"}
        r = await client.get("/api/v1/auth/check", headers=right)
        assert r.status_code == 200 and r.json() == {"ok": True}

    async def test_writes_need_a_key(self, client, keys):
        assert (await client.post("/api/v1/ingest", json={"lines": [LINE]})).status_code == 401
        assert (await client.post("/api/v1/runbooks/index")).status_code == 401
        r = await client.patch("/api/v1/incidents/1", json={"status": "resolved"})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        assert (await client.post("/api/v1/detect", json={})).status_code == 401

    async def test_wrong_key_is_refused(self, client, keys):
        r = await client.post(
            "/api/v1/detect", json={}, headers={"Authorization": "Bearer nope"}
        )
        assert r.status_code == 401

    async def test_reads_are_private_unless_opened(self, client, keys):
        assert (await client.get("/api/v1/incidents")).status_code == 401
        keys.public_read = True
        assert (await client.get("/api/v1/incidents")).status_code != 401

    async def test_header_keys_work(self, client, keys, clean_db):
        for headers in ({"Authorization": f"Bearer {KEY}"}, {"X-API-Key": KEY}):
            r = await client.post("/api/v1/ingest", json={"lines": [LINE]}, headers=headers)
            assert r.status_code == 200

    @pytest.mark.parametrize("param", ["api_key", "token", "key", "access_token"])
    async def test_keys_in_the_url_are_refused(self, client, param):
        r = await client.post(f"/api/v1/ingest?{param}={KEY}", json={"lines": [LINE]})
        assert r.status_code == 400
        assert "header" in r.json()["detail"]

    async def test_health_and_config_stay_public(self, client, keys, clean_db):
        assert (await client.get("/api/v1/health")).status_code == 200
        config = (await client.get("/api/v1/config")).json()
        assert config["read_requires_key"] and config["write_requires_key"]

    async def test_metrics_need_a_key(self, client, keys, clean_db):
        assert (await client.get("/metrics")).status_code == 401
        line = "2026-01-01T00:00:00Z svc ERROR login failed password=hunter2"
        await client.post("/api/v1/ingest", json={"lines": [line]}, headers={"X-API-Key": KEY})
        r = await client.get("/metrics", headers={"X-API-Key": KEY})
        assert r.status_code == 200
        assert 'sentinel_http_requests_total{method="POST",route="/api/v1/ingest"' in r.text
        assert 'sentinel_ingest_events_total{result="stored"}' in r.text
        assert 'sentinel_redactions_total{kind="secret"}' in r.text

    async def test_production_refuses_to_start_without_keys(self, settings):
        settings.environment = "production"
        with pytest.raises(RuntimeError, match="API_KEYS"):
            async with app.router.lifespan_context(app):
                pass


class TestBodyLimit:  # audit/repro_09
    async def test_declared_oversize_body_is_413(self, client, settings):
        settings.max_body_bytes = 1000
        r = await client.post("/api/v1/ingest", json={"lines": ["x" * 2000]})
        assert r.status_code == 413

    async def test_streamed_oversize_body_is_413(self, client, settings):
        settings.max_body_bytes = 1000

        async def chunks():
            for _ in range(10):
                yield b"x" * 500

        # No Content-Length: the limit must be enforced while reading
        r = await client.post(
            "/api/v1/ingest", content=chunks(), headers={"content-type": "application/json"}
        )
        assert r.status_code == 413


class TestSecurityHeaders:  # audit/repro_09
    async def test_every_response_has_them(self, client):
        for r in (
            await client.get("/api/v1/health"),
            await client.post("/api/v1/ingest?token=x", json={}),  # an error response too
        ):
            csp = r.headers["content-security-policy"]
            assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["referrer-policy"] == "no-referrer"


class TestWebSocketAuth:
    def test_private_socket_needs_an_auth_message(self, keys):
        with TestClient(app) as tc, tc.websocket_connect("/api/v1/ws/alerts") as ws:
            ws.send_text('{"type": "auth", "key": "wrong"}')
            msg = ws.receive()
            assert msg["type"] == "websocket.close" and msg["code"] == 4401

    def test_valid_auth_message_is_accepted(self, keys):
        keys.realtime_enabled = False
        with TestClient(app) as tc, tc.websocket_connect("/api/v1/ws/alerts") as ws:
            ws.send_text(f'{{"type": "auth", "key": "{KEY}"}}')
            ws.send_text("ping")  # still open: a closed socket would raise here


@pytest.mark.usefixtures("clean_db")
class TestLLMBudget:  # audit/repro_06
    async def test_concurrent_reservations_never_exceed_the_cap(self):
        day = date(2026, 1, 1)

        async def one() -> bool:
            async with async_session_factory() as s:
                return await reserve(s, day, 100, max_calls=5, max_tokens=10_000)

        granted = await asyncio.gather(*(one() for _ in range(25)))
        assert sum(granted) == 5
        await engine.dispose()

    async def test_token_budget_is_enforced(self):
        day = date(2026, 1, 2)
        async with async_session_factory() as s:
            assert await reserve(s, day, 600, max_calls=99, max_tokens=1000)
            assert not await reserve(s, day, 600, max_calls=99, max_tokens=1000)
        await engine.dispose()

    async def test_over_budget_triage_falls_back_without_calling(self, client):
        class Counting:
            model = "fake"
            calls = 0

            async def complete_json(self, system: str, user: str) -> str:
                Counting.calls += 1
                return '{"summary": "s", "root_cause": "r", "confidence": "low"}'

        llm = BudgetedLLM(
            Counting(),
            async_session_factory,
            max_calls_per_day=2,
            max_tokens_per_day=10**9,
            max_output_tokens=100,
        )
        app.dependency_overrides[get_llm_dep] = lambda: llm
        app.dependency_overrides[get_embedder_dep] = HashingEmbedder
        try:
            start = datetime.now(tz=UTC).replace(second=0, microsecond=0) - timedelta(minutes=40)
            lines = [
                f"{(start + timedelta(minutes=m, seconds=s)).isoformat()} svc-{i} "
                f"{'ERROR' if m >= 35 else 'INFO'} upstream timeout"
                for i in range(5)
                for m in range(40)
                for s in (0, 20, 40)
            ]
            await client.post("/api/v1/ingest", json={"lines": lines})
            r = await client.post("/api/v1/detect", json={"detectors": ["error_burst"]})
            found = r.json()
            methods = []
            for a in found["anomalies"]:
                r = await client.post(f"/api/v1/anomalies/{a['id']}/triage")
                methods.append(r.json()["triage_method"])
        finally:
            app.dependency_overrides.pop(get_llm_dep, None)
            app.dependency_overrides.pop(get_embedder_dep, None)
        assert Counting.calls == 2
        assert methods.count("llm") == 2 and methods.count("fallback") == len(methods) - 2

    async def test_budget_error_is_raised_before_the_call(self):
        class Never:
            model = "never"

            async def complete_json(self, system: str, user: str) -> str:
                raise AssertionError("must not be called")

        llm = BudgetedLLM(
            Never(), async_session_factory, max_calls_per_day=0,
            max_tokens_per_day=1, max_output_tokens=1,
        )
        with pytest.raises(BudgetExceededError):
            await llm.complete_json("s", "u")
        await engine.dispose()


@pytest.mark.usefixtures("clean_db")
async def test_detection_does_not_stall_the_event_loop(client):  # audit/repro_07
    t0 = datetime.now(tz=UTC) - timedelta(hours=1)
    lines = [
        f"{(t0 + timedelta(minutes=m, seconds=s)).isoformat()} svc-{i:02d} INFO ok"
        for i in range(60)
        for m in range(30)
        for s in (0, 30)
    ]
    await client.post("/api/v1/ingest", json={"lines": lines})

    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    # iforest costs ~0.2 s per service: ~12 s of CPU on the loop before the fix
    r = await client.post("/api/v1/detect", json={"since": t0.isoformat()})
    stop.set()
    await task
    assert r.status_code == 200
    assert max(gaps) < 2.0

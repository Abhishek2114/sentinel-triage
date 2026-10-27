"""Shared test fixtures."""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import get_redis_factory
from app.core.db import engine
from app.main import app


class RecordingRedis:
    """Stands in for Redis in API tests and remembers what was published."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.events.append(json.loads(message))
        return 1

    def pubsub(self) -> Any:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


@pytest.fixture
def published() -> RecordingRedis:
    """Live events the API published during the test."""
    return RecordingRedis()


@pytest.fixture
async def client(published: RecordingRedis) -> AsyncIterator[AsyncClient]:
    """Async test client for the FastAPI app (lifespan not run, so no Redis relay)."""
    app.dependency_overrides[get_redis_factory] = lambda: lambda: published
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_redis_factory, None)
    # Each test runs in its own event loop; pooled asyncpg connections can't
    # be reused across loops, so drop them between tests.
    await engine.dispose()


@pytest.fixture
async def clean_db() -> AsyncIterator[None]:
    """An empty, migrated database – or skip when none is reachable.

    CI provides one (DATABASE_URL + `alembic upgrade head`); locally these
    tests skip unless you've started Postgres.
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE incidents, anomalies, log_events, runbook_chunks "
                    "RESTART IDENTITY CASCADE"
                )
            )
    except Exception as exc:  # unreachable, or not migrated yet
        await engine.dispose()
        pytest.skip(f"Postgres not available: {exc.__class__.__name__}")
    yield
    await engine.dispose()

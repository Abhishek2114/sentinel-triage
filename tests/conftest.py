"""Shared test fixtures."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import engine
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
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
                text("TRUNCATE incidents, anomalies, log_events RESTART IDENTITY CASCADE")
            )
    except Exception as exc:  # unreachable, or not migrated yet
        await engine.dispose()
        pytest.skip(f"Postgres not available: {exc.__class__.__name__}")
    yield
    await engine.dispose()

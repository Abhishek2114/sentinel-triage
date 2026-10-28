"""Shared plumbing for the audit reproductions.

Each ``repro_*.py`` script exercises one suspected defect against the real code
and ends with a verdict line: ``REPRODUCED`` (exit 1) or ``NOT REPRODUCED``
(exit 0). Re-running them after a fix is the before/after evidence.

DB-backed scripts need DATABASE_URL pointing at a migrated Postgres; they
TRUNCATE its tables, so never point them at real data.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


def verdict(defect: bool, what: str) -> None:
    print(f"\n{'REPRODUCED' if defect else 'NOT REPRODUCED'}: {what}")
    sys.exit(1 if defect else 0)


class NullRedis:
    async def publish(self, channel: str, message: str) -> int:
        return 1

    def pubsub(self) -> Any:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


@asynccontextmanager
async def api(overrides: dict[Any, Any] | None = None) -> AsyncIterator[AsyncClient]:
    """An in-process client for the real app on a truncated database."""
    from app.api.deps import get_redis_factory
    from app.core.db import engine
    from app.main import app

    async with engine.begin() as conn:
        tables = await conn.run_sync(
            lambda c: [t for t in c.dialect.get_table_names(c) if t != "alembic_version"]
        )
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    app.dependency_overrides[get_redis_factory] = lambda: NullRedis
    for dep, fn in (overrides or {}).items():
        app.dependency_overrides[dep] = fn
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=120
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

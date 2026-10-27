"""Live incident events: publish to Redis, relay to WebSocket clients.

Incidents are created in Celery workers and in API processes, but each
WebSocket connection lives in one API process. So whoever changes an incident
publishes an event to a Redis channel, and every API process subscribes and
forwards events to its own clients (``app/api/ws.py``).

Publishing is best effort: Redis being down must never fail triage, so errors
are logged and dropped. The dashboard also refetches when it reconnects.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.incident import Incident
from app.schemas import IncidentRead

log = get_logger(__name__)

CHANNEL = "sentinel:incidents"
EventType = Literal["incident.created", "incident.updated"]


class RedisLike(Protocol):
    async def publish(self, channel: str, message: str) -> Any: ...
    def pubsub(self) -> Any: ...
    async def aclose(self) -> None: ...


RedisFactory = Callable[[], RedisLike]


def redis_factory(url: str) -> RedisFactory:
    return lambda: Redis.from_url(url)


async def incident_event(
    session: AsyncSession, incident_id: int, event_type: EventType
) -> dict[str, Any] | None:
    """The message the dashboard receives: event type + the incident as the API returns it."""
    incident = await session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        # Rows change through UPDATE statements; don't serve a stale cached object
        .execution_options(populate_existing=True)
    )
    if incident is None:
        return None
    return {
        "type": event_type,
        "incident": IncidentRead.model_validate(incident).model_dump(mode="json"),
    }


async def publish(factory: RedisFactory, event: dict[str, Any]) -> bool:
    """Publish one event. Returns False (and logs) instead of raising."""
    client = factory()
    try:
        await client.publish(CHANNEL, json.dumps(event))
        return True
    except (RedisError, OSError) as exc:
        log.warning("incident_event_not_published", error=str(exc), type=event.get("type"))
        return False
    finally:
        await client.aclose()


async def publish_incident(
    session: AsyncSession, factory: RedisFactory, incident_id: int, event_type: EventType
) -> bool:
    event = await incident_event(session, incident_id, event_type)
    return event is not None and await publish(factory, event)


async def relay(
    factory: RedisFactory,
    deliver: Callable[[str], Awaitable[None]],
    *,
    min_backoff: float = 1.0,
    max_backoff: float = 30.0,
) -> None:
    """Forward every message on the channel to ``deliver``, forever.

    Reconnects with exponential backoff when Redis is unreachable or drops
    the connection. Cancel the task to stop it.
    """
    backoff = min_backoff
    while True:
        client = factory()
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(CHANNEL)
            log.info("realtime_relay_subscribed", channel=CHANNEL)
            backoff = min_backoff
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message["data"]
                await deliver(data.decode() if isinstance(data, bytes) else str(data))
        except asyncio.CancelledError:
            raise
        except (RedisError, OSError) as exc:
            log.warning("realtime_relay_disconnected", error=str(exc), retry_in=backoff)
        finally:
            try:
                await pubsub.aclose()
                await client.aclose()
            except (RedisError, OSError):
                pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)

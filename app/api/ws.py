"""WebSocket endpoint for live incident alerts."""

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

router = APIRouter(tags=["websocket"])
log = get_logger(__name__)

# In-memory set of connected clients. This only reaches clients connected to
# *this* process; Celery workers and other uvicorn workers need a shared
# channel (e.g. Redis pub/sub) to push incidents here.
_connections: set[WebSocket] = set()


@router.websocket("/ws/alerts")
async def alert_stream(websocket: WebSocket) -> None:
    """Stream new incidents to connected dashboard clients."""
    await websocket.accept()
    _connections.add(websocket)
    log.info("ws_client_connected", total=len(_connections))

    try:
        # Keep connection alive – listen for pings or client messages
        while True:
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        # Also runs on unexpected errors, so dead sockets never stay registered
        _connections.discard(websocket)
        log.info("ws_client_disconnected", total=len(_connections))


async def broadcast_incident(incident_data: dict[str, Any]) -> None:
    """Push an incident payload to all connected WebSocket clients."""
    if not _connections:
        return

    payload = json.dumps(incident_data, default=str)
    dead: list[WebSocket] = []

    # Iterate over a snapshot: clients can connect/disconnect while we await send
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        _connections.discard(ws)

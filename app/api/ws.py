"""WebSocket endpoint for live incident alerts.

Each API process keeps its own set of sockets. Events reach them through
``app.realtime.relay``, which forwards the Redis channel to ``broadcast``.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

router = APIRouter(tags=["websocket"])
log = get_logger(__name__)

# Clients connected to *this* process
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


async def broadcast(payload: str) -> None:
    """Send an already-serialised event to every client of this process."""
    if not _connections:
        return

    dead: list[WebSocket] = []

    # Iterate over a snapshot: clients can connect/disconnect while we await send
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        _connections.discard(ws)

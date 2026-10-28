"""Access control and HTTP hardening for running on the public internet.

- API keys travel only in headers (``Authorization: Bearer`` or ``X-API-Key``).
  A credential-looking query parameter is refused outright, because URLs end
  up in proxy logs, browser history and Referer headers.
- Writes always need a key once keys are configured; reads can be opened to
  everyone (``PUBLIC_READ`` or ``DEMO_MODE``).
- Request bodies are capped while they stream in, so a client that lies about
  (or omits) Content-Length is cut off too.
- Every response carries a strict Content-Security-Policy and friends.
"""

from __future__ import annotations

import hmac
import json
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import HTTPException, Request, WebSocket, status

from app.core.config import Settings, get_settings
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

CREDENTIAL_PARAMS = frozenset(
    {"api_key", "apikey", "api-key", "key", "token", "access_token", "auth", "password"}
)


def presented_key(headers: Any) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return str(auth[7:].strip()) or None
    key = headers.get("x-api-key")
    return str(key) if key else None


def key_is_valid(key: str | None, settings: Settings) -> bool:
    if not key:
        return False
    # Compare against every key, without stopping early, in constant time
    matches = [hmac.compare_digest(key.encode(), k.encode()) for k in settings.api_keys]
    return any(matches)


def auth_enabled(settings: Settings) -> bool:
    return bool(settings.api_keys)


def reads_are_public(settings: Settings) -> bool:
    return not auth_enabled(settings) or settings.public_read or settings.demo_mode


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Send an API key as 'Authorization: Bearer <key>' or 'X-API-Key'.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_key(request: Request) -> None:
    """Writes, and anything that costs money or changes state."""
    settings = get_settings()
    if auth_enabled(settings) and not key_is_valid(presented_key(request.headers), settings):
        raise _unauthorized()


async def require_read_access(request: Request) -> None:
    settings = get_settings()
    if not reads_are_public(settings) and not key_is_valid(
        presented_key(request.headers), settings
    ):
        raise _unauthorized()


async def websocket_authorized(websocket: WebSocket, timeout: float = 5.0) -> bool:
    """Browsers can't set headers on a WebSocket, and keys never go in URLs,
    so a private deployment expects ``{"type": "auth", "key": "..."}`` as the
    first message. Call after ``accept()``."""
    import asyncio

    settings = get_settings()
    if reads_are_public(settings):
        return True
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout)
        msg = json.loads(raw)
    except (TimeoutError, ValueError):
        return False
    return isinstance(msg, dict) and msg.get("type") == "auth" and key_is_valid(
        str(msg.get("key", "")), settings
    )


# ── Middleware (plain ASGI, so they also see streamed bodies) ──────────


async def _json_response(send: Send, code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _BodyTooLargeError(Exception):
    pass


class RequestGuardMiddleware:
    """Refuse credentials in the URL and bodies over ``max_body_bytes``."""

    def __init__(self, app: ASGIApp, max_body_bytes: int | None = None) -> None:
        self.app = app
        # None: read Settings.max_body_bytes on every request
        self._fixed_limit = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        from urllib.parse import parse_qsl

        query = scope.get("query_string", b"").decode("latin-1")
        if any(k.lower() in CREDENTIAL_PARAMS for k, _ in parse_qsl(query, True)):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4400})
                return
            await _json_response(
                send,
                400,
                "Credentials are not accepted in the URL. "
                "Use the Authorization or X-API-Key header.",
            )
            return
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        limit = self._fixed_limit or get_settings().max_body_bytes
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            await _json_response(send, 413, f"Body larger than {limit} bytes")
            return

        received = 0
        too_large = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # FastAPI turns a failed body read into its own 400; answer 413 instead
            if too_large:
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await _json_response(send, 413, f"Body larger than {limit} bytes")
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLargeError:
            if not started:
                await _json_response(send, 413, f"Body larger than {limit} bytes")


_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
# Swagger UI loads its bundle from a CDN and boots with an inline script.
# /docs is off in production, so this only applies in development.
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)
_STATIC_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        csp = _DOCS_CSP if path in ("/docs", "/redoc") else _CSP

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                extra = [(b"content-security-policy", csp.encode()), *_STATIC_HEADERS]
                headers += [(k, v) for k, v in extra if k not in present]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, with_headers)


class MetricsMiddleware:
    """Request count and latency, labelled by route template (bounded cardinality)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        code = 500
        start = time.perf_counter()

        async def recording_send(message: Message) -> None:
            nonlocal code
            if message["type"] == "http.response.start":
                code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, recording_send)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            # Routes from an included router report their path without its prefix
            prefix = get_settings().api_v1_prefix
            if scope.get("path", "").startswith(prefix) and not template.startswith(prefix):
                template = prefix + template
            HTTP_REQUESTS.labels(scope.get("method", "?"), template, str(code)).inc()
            HTTP_LATENCY.labels(template).observe(time.perf_counter() - start)

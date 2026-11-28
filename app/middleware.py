"""Pure ASGI middleware: request logging and security headers."""

from __future__ import annotations

import json
import logging
import time

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("app.request")

APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
# Swagger UI is served from jsDelivr and boots with an inline script.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        csp = DOCS_CSP if path.startswith(DOCS_PATHS) else APP_CSP

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", csp)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestLogMiddleware:
    """One JSON line per request: method, path, status, duration_ms (no query string)."""

    def __init__(self, app: ASGIApp, trusted_proxy_hops: int = 0) -> None:
        self.app = app
        self.trusted_proxy_hops = trusted_proxy_hops

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception(
                "unhandled error",
                extra={"method": scope["method"], "path": scope["path"]},
            )
            raise
        finally:
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": duration,
                    "client": _client(scope, self.trusted_proxy_hops),
                },
            )


def _client(scope: Scope, hops: int) -> str:
    client = scope.get("client")
    peer = str(client[0]) if client else "unknown"
    if hops <= 0:
        return peer
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            raw: bytes = value
            parts = [p.strip() for p in raw.decode("latin-1").split(",") if p.strip()]
            if len(parts) >= hops:
                return parts[-hops]
    return peer


class BodySizeLimitMiddleware:
    """Refuse request bodies over `limit` bytes before FastAPI reads and parses them.

    Without this, uvicorn buffers any body (a 100 MB JSON POST to /api/auth/login, no
    account needed) and pydantic parses it in full. Checks Content-Length up front and
    counts streamed bytes for chunked requests.
    """

    def __init__(self, app: ASGIApp, *, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > self.limit
                except ValueError:
                    too_big = False
                if too_big:
                    await _reject(send, self.limit)
                    return
        received = 0
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _BodyTooLargeError:
            if not started:
                await _reject(send, self.limit)


class _BodyTooLargeError(Exception):
    pass


async def _reject(send: Send, limit: int) -> None:
    body = json.dumps({"detail": f"Request body is larger than {limit // 1024} KiB"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

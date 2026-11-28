"""BodySizeLimitMiddleware: oversized bodies get 413 before the app reads them."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.middleware import BodySizeLimitMiddleware


async def echo(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"size": len(body)})


def client(limit: int) -> httpx.AsyncClient:
    # Registered like create_app() does (inside ServerErrorMiddleware, outside the router).
    app = Starlette(
        routes=[Route("/echo", echo, methods=["POST"])],
        middleware=[Middleware(BodySizeLimitMiddleware, limit=limit)],
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_allows_bodies_up_to_the_limit() -> None:
    async with client(10) as c:
        response = await c.post("/echo", content=b"x" * 10)
    assert response.status_code == 200
    assert response.json() == {"size": 10}


@pytest.mark.asyncio
async def test_rejects_by_content_length() -> None:
    async with client(10) as c:
        response = await c.post("/echo", content=b"x" * 11)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_rejects_chunked_bodies_while_streaming() -> None:
    async def chunks():  # no Content-Length: httpx sends Transfer-Encoding: chunked
        for _ in range(5):
            yield b"x" * 4

    async with client(10) as c:
        response = await c.post("/echo", content=chunks())
    assert response.status_code == 413

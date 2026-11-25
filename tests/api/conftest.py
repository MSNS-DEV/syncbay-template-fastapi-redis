"""API tests: the real app in-process (httpx ASGITransport) against the compose Postgres/Redis.

Start the services first:
    docker compose -f compose.yaml -f compose.test.yaml up -d --wait postgres redis
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.main import create_app
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://notes:notes@127.0.0.1:15101/notes")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:15102/0")
SECRET = "api-test-secret-0123456789abcdef0123456789"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": DATABASE_URL,
        "redis_url": REDIS_URL,
        "jwt_secret": SECRET,
        "cookie_secure": False,
        "seed_demo_data": False,
        "rate_limit_auth_per_minute": 1000,
        "rate_limit_write_per_minute": 1000,
        "log_level": "warning",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="session", autouse=True)
def migrated() -> None:
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "REDIS_URL": REDIS_URL, "JWT_SECRET": SECRET}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )


AppFactory = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest_asyncio.fixture
async def make_client() -> AsyncIterator[AppFactory]:
    """Build a client for an app with custom settings; each gets a unique client IP."""
    stack: list[tuple[FastAPI, httpx.AsyncClient, Any]] = []

    async def factory(**overrides: Any) -> httpx.AsyncClient:
        app = create_app(make_settings(**overrides))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        ip = f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
        transport = httpx.ASGITransport(app=app, client=(ip, 4000))
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        stack.append((app, client, lifespan))
        return client

    yield factory
    for _app, client, lifespan in reversed(stack):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


@pytest_asyncio.fixture
async def client(make_client: AppFactory) -> httpx.AsyncClient:
    return await make_client()


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


async def register(client: httpx.AsyncClient, email: str | None = None) -> dict[str, Any]:
    res = await client.post(
        "/api/auth/register", json={"email": email or unique_email(), "password": "pa55word!"}
    )
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}

"""Shared FastAPI dependencies: settings, DB session, Redis, current user, rate limits."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cache import NoteListCache
from app.ratelimit import RateLimiter, RateLimitResult
from app.security import TokenError, decode_token
from app.settings import Settings


@dataclass(slots=True)
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    limiter: RateLimiter
    cache: NoteListCache


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.services
    return state


State = Annotated[AppState, Depends(get_state)]


async def get_session(state: State) -> AsyncIterator[AsyncSession]:
    async with state.sessions() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/auth/login")


async def current_user_id(
    state: State,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        claims = decode_token(
            credentials.credentials, secret=state.settings.jwt_secret, expected_type="access"
        )
    except TokenError:
        raise unauthorized from None
    return claims.sub


CurrentUserId = Annotated[uuid.UUID, Depends(current_user_id)]


def client_ip(request: Request, trusted_hops: int) -> str:
    """The client address, taking `trusted_hops` proxies that append to X-Forwarded-For."""
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    header = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in header.split(",") if h.strip()]
    if len(hops) < trusted_hops:
        return peer
    candidate = hops[-trusted_hops]
    return candidate if _is_ip(candidate) else peer


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _apply(result: RateLimitResult, response: Response) -> None:
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, try again later",
            headers={
                "Retry-After": str(result.retry_after),
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": "0",
            },
        )


async def limit_auth(request: Request, response: Response, state: State) -> None:
    ip = client_ip(request, state.settings.trusted_proxy_hops)
    result = await state.limiter.hit("auth", ip, state.settings.rate_limit_auth_per_minute)
    _apply(result, response)


async def limit_writes(response: Response, state: State, user_id: CurrentUserId) -> uuid.UUID:
    result = await state.limiter.hit(
        "write", str(user_id), state.settings.rate_limit_write_per_minute
    )
    _apply(result, response)
    return user_id


WriterId = Annotated[uuid.UUID, Depends(limit_writes)]

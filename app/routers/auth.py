"""Register, log in, refresh (with rotation), log out, current user."""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import AppState, CurrentUserId, Session, State, limit_auth
from app.models import User
from app.schemas import LoginIn, RefreshIn, RegisterIn, TokenPair, UserOut
from app.security import (
    TokenClaims,
    TokenError,
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    new_csrf_token,
    new_family_id,
    verify_password,
)
from app.seed import seed_welcome_notes

log = logging.getLogger("app.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
COOKIE_PATH = "/api/auth"


def _rt_key(jti: str) -> str:
    return f"rt:{jti}"


def _family_key(family: str) -> str:
    return f"rtfam:{family}"


async def _store_refresh(redis: Redis, claims: TokenClaims, ttl: int) -> None:
    assert claims.family is not None  # noqa: S101 - refresh tokens always carry a family
    pipe = redis.pipeline(transaction=True)
    pipe.set(_rt_key(claims.jti), f"{claims.sub}:{claims.family}", ex=ttl)
    pipe.sadd(_family_key(claims.family), claims.jti)
    pipe.expire(_family_key(claims.family), ttl)
    await pipe.execute()


async def _revoke_family(redis: Redis, family: str) -> None:
    members = await redis.smembers(_family_key(family))
    keys = [_rt_key(m.decode() if isinstance(m, bytes) else str(m)) for m in members]
    await redis.delete(*keys, _family_key(family))


async def _issue(
    state: AppState, response: Response, user: User, family: str | None = None
) -> TokenPair:
    s = state.settings
    access, _ = create_token(
        secret=s.jwt_secret,
        user_id=user.id,
        token_type="access",
        ttl_seconds=s.access_token_ttl_seconds,
    )
    refresh, claims = create_token(
        secret=s.jwt_secret,
        user_id=user.id,
        token_type="refresh",
        ttl_seconds=s.refresh_token_ttl_seconds,
        family=family or new_family_id(),
    )
    await _store_refresh(state.redis, claims, s.refresh_token_ttl_seconds)
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=s.refresh_token_ttl_seconds,
        path=COOKIE_PATH,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(),
        max_age=s.refresh_token_ttl_seconds,
        path="/",
        httponly=False,
        secure=s.cookie_secure,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=s.access_token_ttl_seconds,
        user=UserOut.model_validate(user),
    )


def _clear_cookies(response: Response, state: AppState) -> None:
    secure = state.settings.cookie_secure
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH, secure=secure, httponly=True)
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure)


def _refresh_token_from(request: Request, body: RefreshIn | None) -> str:
    """Body token (API clients) wins; the cookie (browsers) requires the CSRF header."""
    if body is not None and body.refresh_token:
        return body.refresh_token
    cookie = request.cookies.get(REFRESH_COOKIE)
    if not cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token missing")
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    csrf_header = request.headers.get(CSRF_HEADER, "")
    if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    return cookie


OptionalRefresh = Annotated[RefreshIn | None, Body()]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenPair,
    dependencies=[Depends(limit_auth)],
)
async def register(data: RegisterIn, response: Response, state: State, db: Session) -> TokenPair:
    user = User(
        id=uuid.uuid4(),
        email=data.email,
        display_name=data.display_name or data.email.split("@", 1)[0][:80],
        password_hash=await asyncio.to_thread(hash_password, data.password),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email exists") from None
    if state.settings.seed_demo_data:
        seed_welcome_notes(db, user.id)
    await db.commit()
    await db.refresh(user)
    log.info("user registered", extra={"user_id": str(user.id)})
    return await _issue(state, response, user)


@router.post("/login", response_model=TokenPair, dependencies=[Depends(limit_auth)])
async def login(data: LoginIn, response: Response, state: State, db: Session) -> TokenPair:
    user = await db.scalar(select(User).where(User.email == data.email))
    stored_hash = user.password_hash if user else None
    if not await asyncio.to_thread(verify_password, stored_hash, data.password) or user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, data.password)
        await db.commit()
    return await _issue(state, response, user)


@router.post("/refresh", response_model=TokenPair, dependencies=[Depends(limit_auth)])
async def refresh(
    request: Request,
    response: Response,
    state: State,
    db: Session,
    body: OptionalRefresh = None,
) -> TokenPair:
    token = _refresh_token_from(request, body)
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    try:
        claims = decode_token(token, secret=state.settings.jwt_secret, expected_type="refresh")
    except TokenError:
        _clear_cookies(response, state)
        raise invalid from None
    assert claims.family is not None  # noqa: S101 - enforced by decode_token
    # Atomically consume the token: a second use finds nothing.
    stored = await state.redis.getdel(_rt_key(claims.jti))
    if stored is None:
        # Reuse of a rotated (or revoked) token: assume theft and kill the whole family.
        await _revoke_family(state.redis, claims.family)
        log.warning("refresh token reuse detected", extra={"user_id": str(claims.sub)})
        raise invalid
    user = await db.get(User, claims.sub)
    if user is None:
        await _revoke_family(state.redis, claims.family)
        raise invalid
    await state.redis.srem(_family_key(claims.family), claims.jti)
    return await _issue(state, response, user, family=claims.family)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, state: State, body: OptionalRefresh = None) -> Response:
    token = _refresh_token_from(request, body)
    try:
        claims = decode_token(token, secret=state.settings.jwt_secret, expected_type="refresh")
    except TokenError:
        claims = None
    if claims is not None and claims.family is not None:
        await _revoke_family(state.redis, claims.family)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookies(out, state)
    return out


@router.get("/me", response_model=UserOut)
async def me(user_id: CurrentUserId, db: Session) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return UserOut.model_validate(user)

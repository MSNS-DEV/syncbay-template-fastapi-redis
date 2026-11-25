"""Password hashing (argon2id) and JWT helpers."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

TokenType = Literal["access", "refresh"]
ALGORITHM = "HS256"
ISSUER = "syncbay-template-fastapi-redis"

_hasher = PasswordHasher()  # argon2id, RFC 9106 low-memory profile defaults
# Verified against when the e-mail is unknown, so a failed login costs the same either way.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


class TokenError(Exception):
    """The token is malformed, expired, has a bad signature or the wrong type."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: uuid.UUID
    type: TokenType
    jti: str
    exp: int
    family: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_token(
    *,
    secret: str,
    user_id: uuid.UUID,
    token_type: TokenType,
    ttl_seconds: int,
    family: str | None = None,
    now: int | None = None,
) -> tuple[str, TokenClaims]:
    issued = int(time.time()) if now is None else now
    claims = TokenClaims(
        sub=user_id,
        type=token_type,
        jti=uuid.uuid4().hex,
        exp=issued + ttl_seconds,
        family=family,
    )
    payload: dict[str, object] = {
        "sub": str(user_id),
        "typ": token_type,
        "jti": claims.jti,
        "iat": issued,
        "exp": claims.exp,
        "iss": ISSUER,
    }
    if family is not None:
        payload["fam"] = family
    return jwt.encode(payload, secret, algorithm=ALGORITHM), claims


def decode_token(token: str, *, secret: str, expected_type: TokenType) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["sub", "typ", "jti", "exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("typ") != expected_type:
        raise TokenError("wrong token type")
    try:
        sub = uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise TokenError("invalid subject") from exc
    family = payload.get("fam")
    if expected_type == "refresh" and not isinstance(family, str):
        raise TokenError("refresh token without family")
    return TokenClaims(
        sub=sub,
        type=expected_type,
        jti=str(payload["jti"]),
        exp=int(payload["exp"]),
        family=family if isinstance(family, str) else None,
    )


def new_family_id() -> str:
    return uuid.uuid4().hex


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)

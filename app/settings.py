"""Configuration, read only from environment variables."""

from __future__ import annotations

import re
import sys
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SSLMODE_TO_ASYNCPG = {
    "disable": False,
    "allow": "allow",
    "prefer": "prefer",
    "require": "require",
    "verify-ca": "verify-ca",
    "verify-full": "verify-full",
}


def to_asyncpg_url(raw: str) -> tuple[URL, dict[str, Any]]:
    """Convert a `postgresql://` / `postgres://` URL into a SQLAlchemy asyncpg URL.

    libpq's `sslmode` query parameter is not understood by asyncpg, so it is removed from the
    URL and returned as the `ssl` connect argument instead.
    """
    value = raw.strip()
    for prefix in ("postgres://", "postgresql://", "postgresql+asyncpg://"):
        if value.startswith(prefix):
            value = "postgresql+asyncpg://" + value[len(prefix) :]
            break
    else:
        raise ValueError("DATABASE_URL must start with postgresql:// or postgres://")
    url = make_url(value)
    connect_args: dict[str, Any] = {}
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if isinstance(sslmode, str):
        if sslmode not in _SSLMODE_TO_ASYNCPG:
            raise ValueError(f"unsupported sslmode {sslmode!r} in DATABASE_URL")
        connect_args["ssl"] = _SSLMODE_TO_ASYNCPG[sslmode]
    return url.set(query=query), connect_args


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    database_url: str = Field(min_length=1)
    redis_url: str = Field(min_length=1)
    jwt_secret: str = Field(min_length=32)

    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    refresh_token_ttl_seconds: int = Field(default=1_209_600, ge=3_600, le=7_776_000)
    rate_limit_auth_per_minute: int = Field(default=10, ge=1)
    rate_limit_write_per_minute: int = Field(default=60, ge=1)
    cache_ttl_seconds: int = Field(default=60, ge=1, le=3_600)
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    cookie_secure: bool = True
    db_pool_size: int = Field(default=5, ge=1, le=50)
    seed_demo_data: bool = True

    brand_name: str = Field(default="Syncbay", min_length=1, max_length=60)
    brand_logo_url: str = ""
    brand_primary: str = "#06b6d4"

    @field_validator("log_level", mode="before")
    @classmethod
    def _lower(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v

    @field_validator("database_url")
    @classmethod
    def _db_url(cls, v: str) -> str:
        to_asyncpg_url(v)
        return v

    @field_validator("redis_url")
    @classmethod
    def _redis_url(cls, v: str) -> str:
        if not v.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("REDIS_URL must start with redis://, rediss:// or unix://")
        return v

    @field_validator("brand_primary")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX_COLOR.match(v):
            raise ValueError("BRAND_PRIMARY must be a hex colour such as #06b6d4")
        return v

    @field_validator("brand_logo_url")
    @classmethod
    def _logo(cls, v: str) -> str:
        if v and not v.startswith(("https://", "http://", "/")):
            raise ValueError("BRAND_LOGO_URL must be an http(s) URL or an absolute path")
        return v

    @property
    def sqlalchemy_url(self) -> URL:
        return to_asyncpg_url(self.database_url)[0]

    @property
    def sqlalchemy_connect_args(self) -> dict[str, Any]:
        return to_asyncpg_url(self.database_url)[1]


def format_settings_error(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for err in exc.errors():
        key = str(err["loc"][0]).upper() if err["loc"] else "?"
        if err["type"] == "missing":
            lines.append(f"  - {key} is required but not set")
        else:
            lines.append(f"  - {key}: {err['msg']}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        print(format_settings_error(exc), file=sys.stderr)
        raise SystemExit(1) from None

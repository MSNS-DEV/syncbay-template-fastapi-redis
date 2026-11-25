import uuid

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.cache import list_key, version_key
from app.deps import client_ip
from app.routers.notes import escape_like
from app.settings import Settings, format_settings_error, to_asyncpg_url

BASE = {
    "database_url": "postgresql://u:p@db:5432/app",
    "redis_url": "redis://cache:6379/0",
    "jwt_secret": "x" * 32,
}


def test_database_url_conversion() -> None:
    url, args = to_asyncpg_url("postgres://u:p@h:5432/d")
    assert url.drivername == "postgresql+asyncpg"
    assert url.database == "d"
    assert args == {}


def test_sslmode_is_moved_to_connect_args() -> None:
    url, args = to_asyncpg_url("postgresql://u:p@h/d?sslmode=require&application_name=x")
    assert "sslmode" not in url.query
    assert url.query["application_name"] == "x"
    assert args == {"ssl": "require"}
    assert to_asyncpg_url("postgresql://h/d?sslmode=disable")[1] == {"ssl": False}


def test_bad_database_url() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        to_asyncpg_url("mysql://h/d")


def test_settings_defaults_and_validation() -> None:
    s = Settings(**BASE)  # type: ignore[arg-type]
    assert s.port == 8000
    assert s.brand_name == "Syncbay"
    assert s.brand_primary == "#06b6d4"
    assert s.sqlalchemy_url.drivername == "postgresql+asyncpg"
    for bad in ({"jwt_secret": "short"}, {"brand_primary": "cyan"}, {"redis_url": "http://x"}):
        with pytest.raises(ValidationError):
            Settings(**{**BASE, **bad})  # type: ignore[arg-type]


def test_missing_settings_message(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings()
    message = format_settings_error(exc.value)
    assert "DATABASE_URL is required but not set" in message
    assert "JWT_SECRET is required but not set" in message


def _request(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_client_ip_ignores_forwarded_for_by_default() -> None:
    assert client_ip(_request("10.0.0.1", "6.6.6.6"), 0) == "10.0.0.1"


def test_client_ip_takes_entry_appended_by_trusted_proxy() -> None:
    # The client spoofed 6.6.6.6; the edge proxy appended the real address 1.2.3.4.
    assert client_ip(_request("10.0.0.1", "6.6.6.6, 1.2.3.4"), 1) == "1.2.3.4"
    assert client_ip(_request("10.0.0.1"), 1) == "10.0.0.1"
    assert client_ip(_request("10.0.0.1", "garbage"), 1) == "10.0.0.1"


def test_cache_keys_are_versioned_and_param_sensitive() -> None:
    uid = uuid.uuid4()
    a = list_key(uid, 1, {"q": "x", "limit": 20})
    assert a == list_key(uid, 1, {"limit": 20, "q": "x"})
    assert a != list_key(uid, 2, {"q": "x", "limit": 20})
    assert a != list_key(uid, 1, {"q": "y", "limit": 20})
    assert version_key(uid) == f"notes:ver:{uid}"


def test_escape_like() -> None:
    assert escape_like("50%_off\\") == "50\\%\\_off\\\\"

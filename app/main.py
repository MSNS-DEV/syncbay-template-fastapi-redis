"""Application factory and lifespan (pools opened at start-up, closed on shutdown)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.cache import NoteListCache
from app.db import create_engine, create_sessionmaker
from app.deps import AppState
from app.logging_config import configure_logging
from app.middleware import RequestLogMiddleware, SecurityHeadersMiddleware
from app.ratelimit import RateLimiter
from app.routers import auth, meta, notes
from app.settings import Settings, get_settings

log = logging.getLogger("app")


def build_state(settings: Settings) -> AppState:
    engine = create_engine(settings)
    redis = Redis.from_url(
        settings.redis_url,
        max_connections=10,
        socket_timeout=3,
        socket_connect_timeout=3,
        health_check_interval=30,
    )
    return AppState(
        settings=settings,
        engine=engine,
        sessions=create_sessionmaker(engine),
        redis=redis,
        limiter=RateLimiter(redis),
        cache=NoteListCache(redis, settings.cache_ttl_seconds),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_state(settings)
        app.state.services = state
        log.info("starting", extra={"port": settings.port})
        try:
            yield
        finally:
            await state.redis.aclose()
            await state.engine.dispose()
            log.info("stopped, pools closed")

    app = FastAPI(
        title="FastAPI & Redis Notes API",
        version="1.0.0",
        description="Notes REST API with JWT auth, PostgreSQL and Redis caching / rate limits.",
        lifespan=lifespan,
    )
    app.include_router(meta.router)
    app.include_router(auth.router)
    app.include_router(notes.router)
    app.mount("/static", StaticFiles(directory=meta.STATIC_DIR), name="static")
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware, trusted_proxy_hops=settings.trusted_proxy_hops)
    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn app.main:app` builds the app lazily, so importing this module needs no env vars.
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)

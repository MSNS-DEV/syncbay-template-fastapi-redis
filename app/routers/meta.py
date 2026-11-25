"""Health check, branding config and the web UI."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from app.deps import State
from app.schemas import BrandConfig, HealthOut

log = logging.getLogger("app.health")
router = APIRouter(tags=["meta"])
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CHECK_TIMEOUT_SECONDS = 2.0


async def _check_db(state: State) -> str:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("health check failed", extra={"check": "db", "error": repr(exc)})
        return "error"
    return "ok"


async def _check_redis(state: State) -> str:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await state.redis.ping()
    except Exception as exc:
        log.warning("health check failed", extra={"check": "redis", "error": repr(exc)})
        return "error"
    return "ok"


@router.get(
    "/health",
    response_model=HealthOut,
    responses={503: {"model": HealthOut, "description": "A dependency is down"}},
)
async def health(state: State) -> JSONResponse:
    db, redis = await asyncio.gather(_check_db(state), _check_redis(state))
    ok = db == "ok" and redis == "ok"
    body = HealthOut(status="ok" if ok else "error", checks={"db": db, "redis": redis})
    return JSONResponse(
        body.model_dump(), status_code=200 if ok else 503, headers={"Cache-Control": "no-store"}
    )


@router.get("/config.json", response_model=BrandConfig)
async def brand_config(state: State) -> JSONResponse:
    s = state.settings
    body = BrandConfig(
        brandName=s.brand_name, brandLogoUrl=s.brand_logo_url, brandPrimary=s.brand_primary
    )
    return JSONResponse(body.model_dump(), headers={"Cache-Control": "no-cache"})


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

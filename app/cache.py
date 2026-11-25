"""Redis read-through cache for note lists, invalidated by bumping a per-user version."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger("app.cache")


def version_key(user_id: uuid.UUID) -> str:
    return f"notes:ver:{user_id}"


def list_key(user_id: uuid.UUID, version: int, params: Mapping[str, object]) -> str:
    canonical = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:32]
    return f"notes:list:{user_id}:v{version}:{digest}"


class NoteListCache:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def _version(self, user_id: uuid.UUID) -> int:
        raw = await self._redis.get(version_key(user_id))
        return int(raw) if raw is not None else 0

    async def get(self, user_id: uuid.UUID, params: Mapping[str, object]) -> tuple[str, str | None]:
        """Return (key, cached JSON or None). On Redis errors the key is empty: skip caching."""
        try:
            key = list_key(user_id, await self._version(user_id), params)
            raw = await self._redis.get(key)
        except RedisError:
            log.warning("cache read failed")
            return "", None
        return key, (raw.decode() if isinstance(raw, bytes) else raw)

    async def put(self, key: str, payload: str) -> None:
        if not key:
            return
        try:
            await self._redis.set(key, payload, ex=self._ttl)
        except RedisError:
            log.warning("cache write failed")

    async def invalidate(self, user_id: uuid.UUID) -> None:
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(version_key(user_id))
            # The version must outlive every cached page that could still be read.
            pipe.expire(version_key(user_id), max(self._ttl * 10, 3600))
            await pipe.execute()
        except RedisError:
            log.warning("cache invalidation failed")

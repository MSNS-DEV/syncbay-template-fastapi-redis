"""Fixed-window rate limiting backed by Redis (`INCR` + `EXPIRE`)."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from redis.exceptions import RedisError

log = logging.getLogger("app.ratelimit")


class CounterStore(Protocol):
    """The subset of the redis-py asyncio client the limiter uses."""

    def incr(self, name: str, amount: int = 1) -> Awaitable[int]: ...

    def expire(self, name: str, time: int) -> Awaitable[bool]: ...


@dataclass(frozen=True, slots=True)
class Window:
    index: int
    reset_in: int  # whole seconds until the window ends, at least 1


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


def current_window(now: float, window_seconds: int) -> Window:
    index = int(now // window_seconds)
    end = (index + 1) * window_seconds
    reset_in = max(1, int(end - now + 0.999))
    return Window(index=index, reset_in=min(reset_in, window_seconds))


def evaluate(count: int, limit: int, window: Window) -> RateLimitResult:
    allowed = count <= limit
    return RateLimitResult(
        allowed=allowed,
        limit=limit,
        remaining=max(0, limit - count),
        retry_after=0 if allowed else window.reset_in,
    )


class RateLimiter:
    def __init__(
        self,
        store: CounterStore,
        *,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._window = window_seconds
        self._clock = clock

    async def hit(self, bucket: str, subject: str, limit: int) -> RateLimitResult:
        window = current_window(self._clock(), self._window)
        key = f"rl:{bucket}:{subject}:{window.index}"
        try:
            count = await self._store.incr(key)
            if count == 1:
                # +1 s so the key outlives the window even with clock skew between instances.
                await self._store.expire(key, self._window + 1)
        except RedisError:
            log.warning("rate limiter unavailable, allowing request", extra={"bucket": bucket})
            return RateLimitResult(allowed=True, limit=limit, remaining=limit, retry_after=0)
        return evaluate(count, limit, window)

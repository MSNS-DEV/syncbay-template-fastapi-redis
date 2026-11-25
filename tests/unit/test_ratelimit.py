import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.ratelimit import RateLimiter, current_window, evaluate


class FakeCounter:
    """In-memory stand-in for the INCR/EXPIRE subset of Redis."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, name: str, amount: int = 1) -> int:
        self.values[name] = self.values.get(name, 0) + amount
        return self.values[name]

    async def expire(self, name: str, time: int) -> bool:
        self.ttls[name] = time
        return True


class BrokenCounter(FakeCounter):
    async def incr(self, name: str, amount: int = 1) -> int:
        raise RedisConnectionError("down")


def test_current_window_boundaries() -> None:
    assert current_window(120.0, 60).index == 2
    assert current_window(120.0, 60).reset_in == 60
    assert current_window(179.2, 60).reset_in == 1
    assert current_window(150.5, 60).reset_in == 30


def test_evaluate() -> None:
    w = current_window(100.0, 60)
    assert evaluate(10, 10, w).allowed
    assert evaluate(10, 10, w).remaining == 0
    blocked = evaluate(11, 10, w)
    assert not blocked.allowed
    assert blocked.retry_after == w.reset_in


async def test_limiter_blocks_after_limit_and_sets_ttl_once() -> None:
    store = FakeCounter()
    limiter = RateLimiter(store, clock=lambda: 1_000.0)
    results = [await limiter.hit("auth", "1.2.3.4", 3) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].retry_after == 20  # window 960..1020
    assert store.ttls == {"rl:auth:1.2.3.4:16": 61}


async def test_limiter_resets_in_next_window() -> None:
    now = [1_000.0]
    limiter = RateLimiter(FakeCounter(), clock=lambda: now[0])
    for _ in range(2):
        await limiter.hit("write", "u1", 2)
    assert not (await limiter.hit("write", "u1", 2)).allowed
    now[0] = 1_020.0
    assert (await limiter.hit("write", "u1", 2)).allowed


async def test_limiter_subjects_and_buckets_are_independent() -> None:
    limiter = RateLimiter(FakeCounter(), clock=lambda: 0.0)
    assert (await limiter.hit("auth", "a", 1)).allowed
    assert not (await limiter.hit("auth", "a", 1)).allowed
    assert (await limiter.hit("auth", "b", 1)).allowed
    assert (await limiter.hit("write", "a", 1)).allowed


@pytest.mark.parametrize("limit", [1, 5])
async def test_limiter_fails_open_when_redis_is_down(limit: int) -> None:
    result = await RateLimiter(BrokenCounter()).hit("auth", "x", limit)
    assert result.allowed
    assert result.remaining == limit

"""Unit tests for the per-source rate limiter and 429-aware retry."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from src.data_client._ratelimit import (
    RetryPolicy,
    TokenBucket,
    _Spec,
    init_ratelimit,
    is_rate_limit_error,
    request_with_retry,
)


class _FakeClock:
    """Monotonic clock under test control."""

    def __init__(self, t0: float = 0.0) -> None:
        self.now = t0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture(autouse=True)
def _reset_shared_bucket() -> Iterator[None]:
    """Each test starts with a fresh, uninitialized shared bucket."""
    from src.data_client import _ratelimit as rl

    rl._SHARED_BUCKET = None
    yield
    rl._SHARED_BUCKET = None


def _controllable_sleep() -> tuple[asyncio.Event, list[asyncio.Task]]:
    """Build a fake sleep that blocks on an Event until the test releases it."""
    release = asyncio.Event()
    pending: list[asyncio.Task] = []

    async def fake_sleep(_secs: float) -> None:
        # Each waiter registers its task; tests inspect pending tasks to
        # confirm throttling, then set `release` to let them complete.
        task = asyncio.current_task()
        if task is not None:
            pending.append(task)
        await release.wait()

    return release, pending, fake_sleep


# ---- TokenBucket -----------------------------------------------------------


@pytest.mark.asyncio
async def test_token_bucket_burst_then_throttle() -> None:
    """A burst-5 bucket allows 5 immediate acquires; the 6th blocks."""
    bucket = TokenBucket({"x": _Spec(rate_per_sec=1.0, burst=5)})
    for _ in range(5):
        await bucket.acquire("x")
    release, pending, sleep = _controllable_sleep()
    from unittest import mock

    with mock.patch.object(bucket._buckets["x"], "_sleep", side_effect=sleep):
        sixth = asyncio.create_task(bucket.acquire("x"))
        await asyncio.sleep(0.01)
        assert not sixth.done()
        assert len(pending) == 1
        release.set()
        await asyncio.wait_for(sixth, timeout=1.0)


@pytest.mark.asyncio
async def test_concurrent_acquires_respect_burst() -> None:
    """50 concurrent acquires against a burst-10 bucket: setup consumed 1
    token, so 9 immediate passes; 40 should block (the 10th task also blocks
    because it needs the refill to reach 1 full token — 0.001 t/s won't
    credit one in 50 ms).
    """
    bucket = TokenBucket({"y": _Spec(rate_per_sec=0.001, burst=10)})
    # Force bucket creation so the mock target exists before we patch it.
    await bucket.acquire("y")
    release, _pending, sleep = _controllable_sleep()
    from unittest import mock

    with mock.patch.object(bucket._buckets["y"], "_sleep", side_effect=sleep):
        tasks = [asyncio.create_task(bucket.acquire("y")) for _ in range(50)]
        await asyncio.sleep(0.05)
        done = [t for t in tasks if t.done()]
        pending_count = sum(1 for t in tasks if not t.done())
        # Setup drained 1 of 10 tokens, so 9 immediate; the rest must block.
        assert len(done) == 9, f"expected 9 immediate, got {len(done)}"
        assert pending_count == 41
        release.set()
        for t in tasks:
            t.cancel()


@pytest.mark.asyncio
async def test_acquire_refills_after_clock_advance() -> None:
    """With a fake clock, advancing time credits tokens so acquires unblock."""
    clock = _FakeClock()
    bucket = TokenBucket({"z": _Spec(rate_per_sec=10.0, burst=1)}, clock=clock)
    await bucket.acquire("z")  # consume the single token
    release, _pending, sleep = _controllable_sleep()
    from unittest import mock

    with mock.patch.object(bucket._buckets["z"], "_sleep", side_effect=sleep):
        task = asyncio.create_task(bucket.acquire("z"))
        await asyncio.sleep(0.01)
        assert not task.done()
        # Advance the clock by 0.5s → 5 tokens credited → waiter unblocks.
        clock.now += 0.5
        release.set()
        await asyncio.wait_for(task, timeout=1.0)


# ---- request_with_retry --------------------------------------------------


@pytest.mark.asyncio
async def test_request_with_retry_retries_on_429() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Futu API request failed (429)")
        return "ok"

    init_ratelimit()
    result = await request_with_retry(
        "futu", flaky, policy=RetryPolicy(max_retries=3, base_delay=0.001)
    )
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_request_with_retry_gives_up_after_max() -> None:
    async def always_429() -> None:
        raise RuntimeError("Futu API request failed (429)")

    init_ratelimit()
    with pytest.raises(RuntimeError, match="429"):
        await request_with_retry(
            "futu", always_429, policy=RetryPolicy(max_retries=2, base_delay=0.001)
        )


@pytest.mark.asyncio
async def test_request_with_retry_default_policy_is_conservative() -> None:
    """Default policy gives up after 3 total attempts (initial + 2 retries)."""
    calls = {"n": 0}

    async def always_429() -> None:
        calls["n"] += 1
        raise RuntimeError("Futu API request failed (429)")

    init_ratelimit()
    with pytest.raises(RuntimeError, match="429"):
        await request_with_retry(
            "futu", always_429, policy=RetryPolicy(base_delay=0.001)
        )
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_request_with_retry_propagates_non_rate_errors() -> None:
    calls = {"n": 0}

    async def boom() -> None:
        calls["n"] += 1
        raise ValueError("network down")

    init_ratelimit()
    with pytest.raises(ValueError):
        await request_with_retry("futu", boom)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_request_with_retry_throttles_via_bucket() -> None:
    """Without the bucket, 5 calls in a tight loop would race; with bucket +
    sleep injection, they serialise through the burst."""
    init_ratelimit()
    calls: list[int] = []

    async def fast() -> int:
        calls.append(len(calls))
        return len(calls)

    for _ in range(5):
        await request_with_retry("futu", fast)
    assert len(calls) == 5


# ---- is_rate_limit_error --------------------------------------------------


def test_is_rate_limit_error_recognises_429_and_futu() -> None:
    assert is_rate_limit_error(RuntimeError("Futu API request failed (429)"))
    assert is_rate_limit_error(RuntimeError("Futu API error ret_code=-12006 msg=..."))
    assert is_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests"))
    assert not is_rate_limit_error(ValueError("timeout"))
    assert not is_rate_limit_error(RuntimeError("permission denied"))
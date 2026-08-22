"""Per-source rate limiting and 429-aware retry for upstream API clients.

Two pieces:

1. ``TokenBucket`` — async token bucket keyed by source name. ``acquire()``
   blocks the calling coroutine until a token is available. The bucket's
   refill clock and its ``sleep`` primitive are injected so tests can drive
   the bucket deterministically without burning real wall time.

2. ``request_with_retry`` — wraps an async ``do_request`` closure with
   429 / Futu ``ret_code=-12006`` detection and exponential backoff that
   honours ``Retry-After`` when the upstream provides it.

The defaults are conservative — free, undocumented endpoints (tencent, sina)
get the smallest budgets so a single cache-refresh storm cannot get the
project's egress IP banned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Spec:
    """Bucket sizing for one upstream.

    ``rate_per_sec`` is the steady-state refill rate; ``burst`` is the bucket
    depth — the maximum number of tokens available immediately, and the cap
    on how many requests may go through after an idle period.
    """
    rate_per_sec: float
    burst: int


# Per-source defaults. Tuned conservatively — data availability trumps
# latency, so the smallest budgets win over the largest. Free / reverse-
# engineered sources (tencent, sina) get the smallest; documented tiers
# (futu, ginlix-data) are still conservative to leave headroom for shared
# egress IP bursts.
_BUCKET_SPECS: dict[str, _Spec] = {
    "futu":        _Spec(rate_per_sec=3.0,  burst=5),
    "eastmoney":   _Spec(rate_per_sec=2.0,  burst=4),
    "tencent":     _Spec(rate_per_sec=2.0,  burst=4),
    "sina":        _Spec(rate_per_sec=2.0,  burst=4),
    "tushare":     _Spec(rate_per_sec=0.5,  burst=2),    # ~30 req/min, wide margin
    "ginlix-data": _Spec(rate_per_sec=5.0,  burst=10),
    "fmp":         _Spec(rate_per_sec=3.0,  burst=6),
    "yfinance":    _Spec(rate_per_sec=2.0,  burst=4),
    # Official list dumps — once/day, serial. Burst 1 so two SZSE xlsx never overlap.
    "szse":        _Spec(rate_per_sec=0.25, burst=1),
    "sse":         _Spec(rate_per_sec=0.25, burst=1),
    "cninfo":      _Spec(rate_per_sec=0.5,  burst=1),
}


class TokenBucket:
    """Async token bucket keyed by source name; lazily created per source.

    ``sleep`` and ``clock`` are injectable — production code uses real
    ``asyncio.sleep`` / ``time.monotonic``; tests pass a coroutine and a
    callable to drive the bucket without real wall time.
    """

    def __init__(
        self,
        specs: dict[str, _Spec] | None = None,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._specs = specs or _BUCKET_SPECS
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or time.monotonic
        self._sleep = sleep

    def _get_or_create(self, name: str) -> _Bucket:
        bucket = self._buckets.get(name)
        if bucket is not None:
            return bucket
        spec = self._specs.get(name, _Spec(rate_per_sec=5.0, burst=10))
        bucket = _Bucket(rate=spec.rate_per_sec, burst=spec.burst,
                         clock=self._clock, sleep=self._sleep)
        self._buckets[name] = bucket
        return bucket

    async def acquire(self, source: str, *, permits: int = 1) -> None:
        """Wait until ``permits`` tokens are available for ``source``."""
        async with self._lock:
            bucket = self._get_or_create(source)
        await bucket.acquire(permits)


class _Bucket:
    """Single-source token bucket with monotonic-clock refill."""

    def __init__(
        self,
        *,
        rate: float,
        burst: int,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] | None,
    ) -> None:
        self._rate = rate          # tokens per second
        self._burst = burst        # bucket depth
        self._tokens = float(burst)
        self._clock = clock
        self._last = clock()
        self._sleep = sleep or asyncio.sleep
        self._cond = asyncio.Condition()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last = now

    async def acquire(self, permits: int = 1) -> None:
        async with self._cond:
            while True:
                self._refill()
                if self._tokens >= permits:
                    self._tokens -= permits
                    return
                deficit = permits - self._tokens
                wait = deficit / self._rate
                # Sleep through the injected primitive so tests can drive
                # the bucket deterministically; release the condition lock
                # so other coroutines can refill when the clock advances.
                self._cond.release()
                try:
                    await self._sleep(wait)
                finally:
                    await self._cond.acquire()
                # Loop again — refill will pick up any clock advance.

    async def drain(self) -> None:
        """Wake all waiters — tests use this after advancing the fake clock."""
        async with self._cond:
            self._cond.notify_all()


# --- Retry-on-429 ----------------------------------------------------------


@dataclass
class RetryPolicy:
    """429 retry policy — conservative to avoid stacking bursts on top of the
    upstream throttling we're already throttled by.

    ``max_retries=2`` caps total attempts at 3 (initial + 2 retries). ``base_delay=2.0``
    gives the upstream room to recover before the first retry; doubling still
    keeps the total worst-case retry window below 30 seconds.
    """
    max_retries: int = 2
    base_delay: float = 2.0   # seconds, doubled each attempt
    max_delay: float = 30.0


def is_rate_limit_error(exc: Exception) -> bool:
    """True if ``exc`` looks like a rate-limit response.

    Detects HTTP 429 (Futu wraps it as ``Futu API request failed (429)``,
    httpx raises with ``429`` in the message) and Futu's own ``ret_code=-12006``.
    """
    msg = str(exc)
    if "429" in msg:
        return True
    if "ret_code=-12006" in msg:
        return True
    low = msg.lower()
    return "rate limit" in low or "rate-limit" in low or "too many requests" in low


async def request_with_retry(
    source: str,
    do_request: Callable[[], Awaitable[Any]],
    *,
    policy: RetryPolicy | None = None,
) -> Any:
    """Call ``do_request()`` with token-bucket throttling and 429 retry.

    The first attempt is gated by the bucket; on a rate-limit error, sleeps
    ``base_delay * 2**attempt`` (capped at ``max_delay``) and retries up to
    ``policy.max_retries`` times. Non-rate-limit exceptions propagate.
    """
    policy = policy or RetryPolicy()
    global_bucket = _SHARED_BUCKET
    if global_bucket is not None:
        await global_bucket.acquire(source)

    delay = policy.base_delay
    for attempt in range(policy.max_retries + 1):
        try:
            return await do_request()
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt == policy.max_retries:
                raise
            logger.info(
                "ratelimit.retry | source=%s attempt=%d/%d delay=%.1fs err=%s",
                source, attempt + 1, policy.max_retries, delay, exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, policy.max_delay)


# Process-wide singleton bucket; set by ``init_ratelimit()`` so application
# code can override the clock for tests before any client request fires.
_SHARED_BUCKET: TokenBucket | None = None


def init_ratelimit(*, clock: Callable[[], float] | None = None,
                   sleep: Callable[[float], Awaitable[None]] | None = None,
                   config: dict[str, dict[str, float | int]] | None = None) -> TokenBucket:
    """Initialize the shared rate-limit bucket; returns it for inspection.

    Args:
        clock: Injectable monotonic clock for testing.
        sleep: Injectable sleep coroutine for testing.
        config: Optional override dict mapping source name to
            ``{"rate_per_sec": X, "burst": Y}``. Merged on top of the
            hardcoded defaults — config values win.
    """
    global _SHARED_BUCKET
    specs = dict(_BUCKET_SPECS)
    if config:
        for name, overrides in config.items():
            if isinstance(overrides, dict):
                rate = overrides.get("rate_per_sec", specs[name].rate_per_sec if name in specs else 5.0)
                burst = overrides.get("burst", specs[name].burst if name in specs else 10)
                specs[name] = _Spec(rate_per_sec=float(rate), burst=int(burst))  # type: ignore[arg-type]
    _SHARED_BUCKET = TokenBucket(specs, clock=clock, sleep=sleep)
    return _SHARED_BUCKET


def get_bucket() -> TokenBucket | None:
    """Return the shared bucket (None until ``init_ratelimit()`` runs)."""
    return _SHARED_BUCKET
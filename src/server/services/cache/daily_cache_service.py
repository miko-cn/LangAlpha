"""Daily EOD stock data caching with envelope metadata and incremental delta refresh.

Same envelope/delta pattern as IntradayCacheService, simplified for daily granularity:
- Single interval (1day) with its own TTL from config.
- Watermark is Unix ms (converted to an exchange-local date only for the
  delta-refresh ``from_date`` window).
- Market hours gating: no refresh when market is fully closed.

The delta-refresh / pinning / dual-read core is shared with IntradayCacheService
via :class:`_SeriesCacheCore` (daily binds ``interval="1day"`` at every call
site); this module owns the daily specifics — the sync full-fetch path and its
single-flight serialization.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.config.settings import get_ohlcv_ttl
from src.data_client import get_market_data_provider
from src.server.database.ohlcv import (
    HEAD_BARS,
    finalized_bars,
    load_daily_bars,
    load_daily_series,
    replace_daily_body,
    split_head,
)
from src.server.services.cache._instrument_clock import clock_for
from src.server.services.cache._ohlcv_envelope import (
    _EMPTY_RESULT_TTL,
    _bar_time,
    _build_envelope,
    _is_stale_date,
    _merge_bars,
    _needs_refresh,
    is_watermark_stale,
    series_identity,
    splice_is_discontinuous,
    watermark_to_date_str,
)
from src.server.services.cache._series_cache_core import (
    _SeriesCacheCore,
    is_live_window,
    spawn_bg_task,
)
from src.utils.cache.redis_cache import get_cache_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache key builder
# ---------------------------------------------------------------------------

class DailyCacheKeyBuilder:
    """Build cache keys for daily OHLCV data."""

    PREFIX = "ohlcv"

    @classmethod
    def _is_live(cls, to_date: Optional[str]) -> bool:
        return is_live_window(to_date)

    @classmethod
    def daily_key(
        cls,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        source: Optional[str] = None,
        is_index: bool = False,
    ) -> str:
        symbol = symbol.upper()
        src = f"{source}:" if source else ""
        market = "index" if is_index else "stock"
        if cls._is_live(to_date):
            return f"{cls.PREFIX}:{src}{market}:{symbol}:1day"
        return f"{cls.PREFIX}:{src}{market}:{symbol}:1day:{from_date}:{to_date}"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DailyFetchResult:
    """Result of a daily data fetch operation."""

    symbol: str
    data: List[Dict[str, Any]]
    cached: bool
    ttl_remaining: Optional[int]
    background_refresh_triggered: bool
    cache_key: Optional[str] = None
    watermark: Optional[int] = None
    complete: Optional[bool] = None
    market_phase: Optional[str] = None
    truncated: Optional[bool] = None
    # v4 envelope header (lineage), carried from the fetch so the router builds
    # the wire Series header without a second cache read.
    header: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DailyCacheService(_SeriesCacheCore):
    """Singleton service for cached daily EOD stock data with delta refresh."""

    _instance: Optional["DailyCacheService"] = None
    _refresh_locks: Dict[str, asyncio.Lock]
    _logger = logger
    _log_delta = "Daily delta refresh"
    _log_discontinuity = "Daily discontinuity"
    _log_adopt = "Daily cache ADOPT v3→v4"
    _capability = "daily"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._refresh_locks = {}
        return cls._instance

    @classmethod
    def get_instance(cls) -> "DailyCacheService":
        return cls()

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _base_ttl() -> int:
        return get_ohlcv_ttl("1day")

    # -- per-service hooks (genuine deltas) -------------------------------

    @staticmethod
    def _cache_client():
        return get_cache_client()

    @staticmethod
    async def _provider():
        return await get_market_data_provider()

    async def _fetch_chain(
        self, provider, symbol, interval, from_date, to_date, is_index, user_id,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        return await provider.get_daily_with_source(
            symbol=symbol, from_date=from_date, to_date=to_date,
            is_index=is_index, user_id=user_id,
        )

    async def _fetch_from(
        self, provider, publisher, symbol, interval, from_date, to_date, is_index, user_id,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        return await provider.get_daily_from(
            publisher, symbol, from_date=from_date, to_date=to_date,
            is_index=is_index, user_id=user_id,
        )

    def _legacy_key(
        self, symbol, interval, from_date, to_date, source, is_index,
    ) -> str:
        return DailyCacheKeyBuilder.daily_key(symbol, from_date, to_date, source=source, is_index=is_index)

    def _records_for_redis(self, bars, interval):
        if interval != "1day":
            return bars
        _, head = split_head(bars, HEAD_BARS)
        return head

    async def _bars_for_delta_refresh(self, envelope, symbol, interval, is_index):
        env = await self._hydrate_envelope(envelope, symbol, is_index)
        return list(env["bars"]) if env else []

    async def _on_series_committed(
        self, symbol, interval, is_index, bars, source, revision, truncated, clock, phase,
    ) -> None:
        if interval != "1day" or truncated or not bars or not source:
            return
        instrument_key, schema = series_identity(symbol, interval, is_index)
        body = finalized_bars(
            bars,
            trading_date=clock.current_trading_date(),
            market_closed=phase == "closed",
            tz=clock.tz,
        )
        if not body:
            return
        await replace_daily_body(
            instrument_key=instrument_key,
            schema=schema,
            publisher=source,
            revision=revision,
            bars=body,
            truncated=False,
        )

    async def _hydrate_envelope(
        self, envelope: Optional[Dict[str, Any]], symbol: str, is_index: bool,
    ) -> Optional[Dict[str, Any]]:
        """Splice the PG body under a head-only Redis envelope. No-op otherwise."""
        if not envelope or not envelope.get("head_only"):
            return envelope
        header = envelope.get("header") or {}
        instrument_key = header.get("instrument_key")
        schema = header.get("schema")
        publisher = header.get("publisher")
        if not instrument_key or not publisher or not schema:
            return envelope
        body = await load_daily_bars(
            instrument_key, schema, publisher, int(header.get("revision") or 0),
        )
        if not body:
            return envelope
        head = envelope.get("bars") or []
        cutoff = _bar_time(head[0]) if head else 0
        spliced = _merge_bars(body, head, cutoff) if cutoff else body + head
        return {**envelope, "bars": spliced}

    async def _fetch_live_daily(
        self,
        normalized: str,
        from_date: Optional[str],
        to_date: Optional[str],
        is_index: bool,
        user_id: Optional[str],
        clock,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool, int]:
        """PG body + watermark delta when we have a durable series; else full fetch."""
        instrument_key, schema = series_identity(normalized, "1day", is_index)
        meta = await load_daily_series(instrument_key, schema)
        if meta and not meta.truncated:
            body = await load_daily_bars(
                instrument_key, schema, meta.publisher, meta.revision,
            )
            if body:
                return await self._delta_from_body(
                    normalized, from_date, to_date, is_index, user_id, clock, meta, body,
                )
        data, source, truncated = await self._pinned_fetch(
            normalized, "1day", from_date, to_date, is_index, user_id,
        )
        return data, source, truncated, 0

    async def _delta_from_body(
        self,
        normalized: str,
        from_date: Optional[str],
        to_date: Optional[str],
        is_index: bool,
        user_id: Optional[str],
        clock,
        meta,
        body: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool, int]:
        delta_from = watermark_to_date_str(meta.watermark, tz=clock.tz)
        data, source, truncated = await self._pinned_fetch(
            normalized, "1day", delta_from, to_date, is_index, user_id,
        )
        if source and source != meta.publisher:
            logger.info(
                "Daily PG body %s: publisher %s → %s, full re-fetch",
                normalized, meta.publisher, source,
            )
            data, source, truncated = await self._pinned_fetch(
                normalized, "1day", from_date, to_date, is_index, user_id,
            )
            return data, source, truncated, meta.revision + 1
        if splice_is_discontinuous(body, data, meta.watermark):
            logger.info("Daily PG body %s: discontinuity → full re-fetch", normalized)
            data, source, truncated = await self._pinned_fetch(
                normalized, "1day", from_date, to_date, is_index, user_id,
            )
            return data, source, truncated, meta.revision + 1
        return _merge_bars(body, data, meta.watermark), source, truncated, meta.revision

    # -- public API -------------------------------------------------------

    async def get_stock_daily(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        is_index: bool = False,
        user_id: Optional[str] = None,
        live: Optional[bool] = None,
    ) -> DailyFetchResult:
        normalized = symbol.lstrip("^").upper()

        base_ttl = self._base_ttl()
        clock = clock_for(normalized, is_index)
        phase = clock.market_phase()

        # --- Try cache (across all known sources) ---
        cache_key, envelope = await self._find_cached(normalized, "1day", from_date, to_date, is_index, live=live)
        is_live = DailyCacheKeyBuilder._is_live(to_date) if live is None else live

        if envelope is not None:
            bg_triggered = False
            # Historical daily envelopes skip live-only staleness checks but
            # still participate in truncated/soft-TTL refresh via is_live.
            if _needs_refresh(
                envelope, base_ttl, interval="1day", is_live=is_live,
                symbol=normalized, is_index=is_index, clock=clock,
            ):
                if is_live and (
                    _is_stale_date(envelope, clock=clock)
                    or is_watermark_stale(envelope, "1day", symbol=normalized, is_index=is_index, clock=clock)
                    or envelope.get("complete")
                ):
                    # Bars are behind the current trading date (stale date or a
                    # watermark that never advanced), a day-boundary transition,
                    # or a completed envelope past market reopen → sync re-fetch.
                    # Sync (not background) because the daily chart fetches this
                    # series once and never polls — a background refresh wouldn't
                    # reach the current request.
                    logger.info("Daily cache %s: stale/complete → sync re-fetch", normalized)
                    envelope = None
                elif is_live:
                    # Normal SWR: return stale bars, refresh in background.
                    bg_triggered = True
                    spawn_bg_task(
                        self._delta_refresh(cache_key, normalized, "1day", is_index=is_index, user_id=user_id)
                    )
                else:
                    # Historical window wants a retry (truncated / soft TTL) —
                    # re-fetch the bounded window synchronously. The unbounded
                    # delta refresh would fetch to the present and grow the
                    # windowed key past its requested range.
                    envelope = None

            if envelope is not None:
                envelope = await self._hydrate_envelope(envelope, normalized, is_index) or envelope
                return self._cached_result(
                    normalized, envelope, cache_key, phase,
                    background_refresh_triggered=bg_triggered,
                )

        # --- Cache miss / stale-discard: serialized full fetch ---
        return await self._full_fetch(
            normalized, from_date, to_date, is_index, user_id, phase, base_ttl, clock,
            live=live,
        )

    @staticmethod
    def _cached_result(
        normalized: str,
        envelope: Dict[str, Any],
        cache_key: Optional[str],
        phase: str,
        *,
        background_refresh_triggered: bool = False,
    ) -> DailyFetchResult:
        """Build a cache-hit result from an envelope."""
        stored_ttl = envelope.get("stored_ttl", 0)
        elapsed = time.time() - envelope.get("fetched_at", 0)
        ttl_remaining = max(0, int(stored_ttl - elapsed)) if stored_ttl else None
        return DailyFetchResult(
            symbol=normalized,
            data=envelope["bars"],
            cached=True,
            ttl_remaining=ttl_remaining,
            background_refresh_triggered=background_refresh_triggered,
            cache_key=cache_key,
            watermark=envelope.get("watermark"),
            complete=envelope.get("complete", False),
            market_phase=phase,
            truncated=envelope.get("truncated"),
            header=envelope.get("header"),
        )

    async def _full_fetch(
        self,
        normalized: str,
        from_date: Optional[str],
        to_date: Optional[str],
        is_index: bool,
        user_id: Optional[str],
        phase: str,
        base_ttl: int,
        clock=None,
        live: Optional[bool] = None,
    ) -> DailyFetchResult:
        """Fetch the full series upstream, serialized per series.

        Concurrent cold/stale readers of the same series contend on one lock
        (mirroring :meth:`_delta_refresh`); the leader fetches and fills the
        cache, and followers re-read the freshly filled envelope instead of
        each firing their own blocking upstream fetch. A cancelled waiter just
        releases the lock — it can't poison the others.
        """
        clock = clock or clock_for(normalized, is_index)
        lock = self._get_refresh_lock(f"full:{normalized}:{from_date}:{to_date}:{int(is_index)}")
        async with lock:
            # Double-check: the leader may have filled the cache while we waited.
            cache_key, envelope = await self._find_cached(normalized, "1day", from_date, to_date, is_index, live=live)
            if envelope is not None and not _needs_refresh(
                envelope, base_ttl, interval="1day",
                is_live=DailyCacheKeyBuilder._is_live(to_date) if live is None else live,
                symbol=normalized, is_index=is_index, clock=clock,
            ):
                envelope = await self._hydrate_envelope(envelope, normalized, is_index) or envelope
                return self._cached_result(normalized, envelope, cache_key, phase)

            cache = get_cache_client()
            try:
                is_live = DailyCacheKeyBuilder._is_live(to_date) if live is None else live
                if is_live:
                    data, source, truncated, revision = await self._fetch_live_daily(
                        normalized, from_date, to_date, is_index, user_id, clock,
                    )
                else:
                    data, source, truncated = await self._pinned_fetch(
                        normalized, "1day", from_date, to_date, is_index, user_id,
                    )
                    revision = 0
                cache_key = self._build_key(normalized, "1day", from_date, to_date, is_index, live=live)

                closed = phase == "closed"
                complete = closed and len(data) > 0
                eff_ttl = self._effective_ttl(base_ttl, complete, clock)
                if not data:
                    eff_ttl = _EMPTY_RESULT_TTL
                instrument_key, schema = series_identity(normalized, "1day", is_index)
                cache_records = self._records_for_redis(data, "1day") if is_live else data
                env = _build_envelope(
                    cache_records, phase, complete, stored_ttl=eff_ttl, truncated=truncated,
                    data_date=clock.current_trading_date(),
                    instrument_key=instrument_key, schema=schema, publisher=source,
                    revision=revision,
                )
                if is_live and len(cache_records) < len(data):
                    env["header"]["coverage"]["head_only"] = True

                await cache.set(cache_key, env, ttl=eff_ttl)
                if source and data:
                    await self._write_pin(normalized, "1day", is_index, source)
                if is_live:
                    await self._on_series_committed(
                        normalized, "1day", is_index, data, source, revision,
                        truncated, clock, phase,
                    )

                return DailyFetchResult(
                    symbol=normalized,
                    data=data,
                    cached=False,
                    ttl_remaining=eff_ttl,
                    background_refresh_triggered=False,
                    cache_key=cache_key,
                    watermark=env["header"]["watermark"],
                    complete=complete,
                    market_phase=phase,
                    truncated=truncated,
                    header=env["header"],
                )

            except Exception as e:
                logger.error(f"Failed to fetch daily data for {normalized}: {e}")
                return DailyFetchResult(
                    symbol=normalized,
                    data=[],
                    cached=False,
                    ttl_remaining=None,
                    background_refresh_triggered=False,
                    market_phase=phase,
                    error=str(e),
                )

"""Daily cache: Postgres body + Redis head, day-boundary delta."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.server.database.ohlcv import OhlcvSeriesMeta
from src.server.services.cache._ohlcv_envelope import _parse_envelope
from src.server.services.cache.daily_cache_service import DailyCacheService

_ET = ZoneInfo("America/New_York")
_FROZEN = datetime(2026, 7, 1, 12, 0, tzinfo=_ET)
_DAY = 86_400_000
# 2025-01-02 00:00 ET — well behind the frozen session so every bar is finalized.
_BASE = int(datetime(2025, 1, 2, tzinfo=_ET).timestamp() * 1000)


def _bar(t, close=10.0):
    return {
        "time": t, "ts_event": t,
        "open": close, "high": close, "low": close, "close": close, "volume": 1,
    }


def _series(n: int, start: int = _BASE) -> list[dict]:
    return [_bar(start + i * _DAY, float(i)) for i in range(n)]


class _FrozenDatetime:
    def __init__(self, now: datetime):
        self._now = now

    def now(self, tz=None):
        return self._now if tz is None else self._now.astimezone(tz)

    def combine(self, *args, **kwargs):
        from datetime import datetime as _dt

        return _dt.combine(*args, **kwargs)


class _StubCache:
    def __init__(self):
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def set(self, key, value, ttl=None):
        self.store[key] = value


class _Provider:
    def __init__(self, bars):
        self.bars = bars
        self.source_names = ["tencent"]
        self.chain_calls = 0
        self.from_dates: list = []
        self.filter_from = True

    def source_names_for(self, symbol, capability=None):
        return list(self.source_names)

    async def get_daily_with_source(self, symbol, from_date, to_date, is_index, user_id):
        self.chain_calls += 1
        self.from_dates.append(from_date)
        if self.filter_from and from_date:
            y, m, d = (int(p) for p in from_date.split("-")[:3])
            start = int(datetime(y, m, d, tzinfo=_ET).timestamp() * 1000)
            return [b for b in self.bars if b["time"] >= start], "tencent", False
        return list(self.bars), "tencent", False

    async def get_daily_from(self, source_name, symbol, from_date=None, to_date=None,
                             is_index=False, user_id=None):
        return await self.get_daily_with_source(symbol, from_date, to_date, is_index, user_id)


class _MemPG:
    def __init__(self):
        self.meta: OhlcvSeriesMeta | None = None
        self.bars: list[dict] = []

    async def load_series(self, instrument_key, schema):
        return self.meta

    async def load_bars(self, instrument_key, schema, publisher, revision):
        if not self.meta:
            return []
        if self.meta.publisher != publisher or self.meta.revision != revision:
            return []
        return list(self.bars)

    async def replace(self, *, instrument_key, schema, publisher, revision, bars, truncated):
        self.bars = list(bars)
        self.meta = OhlcvSeriesMeta(
            instrument_key=instrument_key,
            schema=schema,
            publisher=publisher,
            revision=revision,
            price_treatment="split_adjusted",
            watermark=max(b["time"] for b in bars),
            truncated=truncated,
        )


@pytest.fixture
def daily_pg(monkeypatch):
    from src.server.services.cache import daily_cache_service as dcs

    DailyCacheService._instance = None
    bars = _series(10)
    provider = _Provider(bars)
    cache = _StubCache()
    pg = _MemPG()

    async def _get_provider():
        return provider

    monkeypatch.setattr(dcs, "get_market_data_provider", _get_provider)
    monkeypatch.setattr(dcs, "get_cache_client", lambda: cache)
    monkeypatch.setattr(dcs, "load_daily_series", pg.load_series)
    monkeypatch.setattr(dcs, "load_daily_bars", pg.load_bars)
    monkeypatch.setattr(dcs, "replace_daily_body", pg.replace)
    monkeypatch.setattr("src.utils.market_hours.datetime", _FrozenDatetime(_FROZEN))
    yield DailyCacheService.get_instance(), provider, cache, pg
    DailyCacheService._instance = None


@pytest.mark.asyncio
async def test_full_fetch_persists_body_and_redis_head(daily_pg):
    svc, _provider, cache, pg = daily_pg
    result = await svc.get_stock_daily("000001.SS", is_index=True)
    assert len(result.data) == 10
    stored = next(v for k, v in cache.store.items() if k.startswith("ohlcv:") and "pin:" not in k)
    parsed = _parse_envelope(stored)
    assert parsed["head_only"] is True
    assert len(parsed["bars"]) == 5
    assert len(pg.bars) == 10
    assert pg.meta.publisher == "tencent"


@pytest.mark.asyncio
async def test_hydrated_read_returns_full_series(daily_pg):
    svc, provider, _cache, _pg = daily_pg
    await svc.get_stock_daily("000001.SS", is_index=True)
    provider.chain_calls = 0
    again = await svc.get_stock_daily("000001.SS", is_index=True)
    assert again.cached is True
    assert len(again.data) == 10
    assert [b["close"] for b in again.data] == [float(i) for i in range(10)]
    assert provider.chain_calls == 0


@pytest.mark.asyncio
async def test_redis_miss_deltas_from_pg_watermark(daily_pg):
    svc, provider, cache, _pg = daily_pg
    await svc.get_stock_daily("000001.SS", is_index=True)
    # Drop the live key; keep the pin so the next miss honors tencent.
    live_keys = [k for k in list(cache.store) if not k.startswith("pin:")]
    for k in live_keys:
        del cache.store[k]
    provider.chain_calls = 0
    provider.from_dates.clear()
    result = await svc.get_stock_daily("000001.SS", is_index=True)
    assert len(result.data) == 10
    assert provider.chain_calls == 1
    assert provider.from_dates[0] is not None  # watermark date, not a full unbounded fetch


@pytest.mark.asyncio
async def test_pg_discontinuity_bumps_revision(daily_pg):
    svc, provider, cache, pg = daily_pg
    await svc.get_stock_daily("000001.SS", is_index=True)
    wm = pg.meta.watermark
    pg.bars = [_bar(wm - _DAY, 1.0), _bar(wm, 2.0)]
    pg.meta = OhlcvSeriesMeta(
        instrument_key=pg.meta.instrument_key,
        schema=pg.meta.schema,
        publisher=pg.meta.publisher,
        revision=0,
        price_treatment="split_adjusted",
        watermark=wm,
        truncated=False,
    )
    # Serve the pre-watermark bar too — vendors often echo a few finalized days.
    provider.filter_from = False
    provider.bars = [_bar(wm - _DAY, 50.0), _bar(wm, 51.0)]
    for k in list(cache.store):
        if not k.startswith("pin:"):
            del cache.store[k]
    await svc.get_stock_daily("000001.SS", is_index=True)
    assert pg.meta.revision == 1

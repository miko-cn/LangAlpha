"""Sina Finance implementation of :class:`MarketDataSource` (keyless).

A-share + HK realtime quotes (Referer-gated, GBK); A-share daily/minute K-line.
Sina reports volume in shares (unlike Tencent/Tushare lots), so no scaling is
needed. HK K-line is not served by Sina — daily/intraday for HK raise and the
chain falls through to Futu.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.data_client.cn.bars import make_bar, sort_ascending, to_ms
from src.data_client.cn.symbols import sina_symbol, split_app_symbol
from src.data_client.market_data_provider import symbol_timezone

from .sina_client import (
    DATALEN_DAILY,
    DATALEN_MINUTE,
    SCALE_DAILY,
    SCALE_MINUTE,
    SinaClient,
    SinaRequestError,
)

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Realtime field indices (comma-separated, same for A-share and HK).
_F_OPEN, _F_PREV, _F_PRICE, _F_HIGH, _F_LOW, _F_VOL = 1, 2, 3, 4, 5, 8


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SinaDataSource:
    """Keyless market data source backed by Sina Finance quotes."""

    @staticmethod
    def _normalize_snapshot(f: list[str]) -> dict[str, Any]:
        def num(i: int) -> float | None:
            try:
                return _as_float(f[i])
            except IndexError:
                return None
        price, prev = num(_F_PRICE), num(_F_PREV)
        change = (price - prev) if (price is not None and prev) else None
        pct = (change / prev * 100.0) if (change is not None and prev) else None
        return {
            "symbol": None,
            "name": f[0] if f else None,
            "price": price,
            "change": change,
            "change_percent": pct,
            "previous_close": prev,
            "open": num(_F_OPEN),
            "high": num(_F_HIGH),
            "low": num(_F_LOW),
            "volume": int(num(_F_VOL) or 0),
            "market_status": None,
            "early_trading_change_percent": None,
            "late_trading_change_percent": None,
        }

    async def get_snapshots(
        self,
        symbols: list[str],
        asset_type: str = "stocks",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        codes = [sina_symbol(s) for s in symbols]
        async with SinaClient() as client:
            rows = await client.get_quotes(codes)
        result = []
        for app_symbol, code in zip(symbols, codes):
            f = rows.get(code)
            if not f:
                continue
            snap = self._normalize_snapshot(f)
            snap["symbol"] = app_symbol
            result.append(snap)
        return result

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        market, _ = split_app_symbol(symbol)
        if market == "hk":
            raise SinaRequestError("Sina K-line has no HK data")
        tz = symbol_timezone(symbol)
        async with SinaClient() as client:
            rows = await client.get_kline(sina_symbol(symbol), SCALE_DAILY, DATALEN_DAILY)
        bars = []
        for r in rows:
            bars.append(make_bar(
                to_ms(r.get("day", ""), tz),
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("volume"),
            ))
        return sort_ascending(bars)

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        market, _ = split_app_symbol(symbol)
        if market == "hk":
            raise SinaRequestError("Sina K-line has no HK data")
        scale = SCALE_MINUTE.get(interval)
        if scale is None:
            raise SinaRequestError(f"interval {interval!r} not supported by Sina")
        tz = symbol_timezone(symbol)
        async with SinaClient() as client:
            rows = await client.get_kline(sina_symbol(symbol), scale, DATALEN_MINUTE)
        bars = []
        for r in rows:
            bars.append(make_bar(
                to_ms(r.get("day", ""), tz),
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("volume"),
            ))
        return sort_ascending(bars)

    async def get_market_status(
        self,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        from src.utils.market_hours import current_market_phase

        phase = current_market_phase()
        return {
            "market": "open" if phase == "open" else "closed",
            "afterHours": phase == "post",
            "earlyHours": phase == "pre",
            "serverTime": datetime.now(_ET).isoformat(),
            "exchanges": None,
        }

    async def close(self) -> None:
        pass


# Backward-compatible alias
SinaPriceProvider = SinaDataSource

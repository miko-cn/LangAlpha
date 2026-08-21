"""Tencent Finance implementation of :class:`MarketDataSource` (keyless).

Free realtime + daily + A-share minute quotes for A-shares and HK. Volume
normalization is market-dependent: A-shares report lots (手, ×100 → shares),
HK reports shares directly. HK minute data is unreliable on Tencent's free
endpoints — intraday for HK raises and the chain falls through to Futu.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.data_client.cn.bars import (
    VOLUME_LOT,
    make_bar,
    minute_stamp_to_ms,
    sort_ascending,
    to_ms,
)
from src.data_client.cn.symbols import split_app_symbol, tencent_symbol
from src.data_client.market_data_provider import symbol_timezone

from .tencent_client import MINUTE_PERIOD, TencentClient, TencentRequestError

logger = logging.getLogger(__name__)

_UTC = UTC
_ET = ZoneInfo("America/New_York")

# Realtime field indices (identical layout for A-share and HK).
# The API is ``~``-delimited; the quoted string starts with a market flag, so
# field N of the flag-split list = index N-1 in the raw ``~`` list.
_F_PRICE, _F_PREV, _F_OPEN, _F_VOL = 3, 4, 5, 6
_F_CHANGE, _F_CHANGE_PCT, _F_HIGH, _F_LOW = 31, 32, 33, 34


def _volume_scale(market: str) -> float:
    """A-share (sh/sz) quotes volume in lots (手); HK in shares."""
    return VOLUME_LOT if market in ("sh", "sz") else 1.0


class TencentDataSource:
    """Keyless market data source backed by Tencent Finance quotes."""

    @staticmethod
    def _normalize_snapshot(code: str, f: list[str]) -> dict[str, Any]:
        market = code[:2]
        def num(i: int) -> float | None:
            try:
                return float(f[i])
            except (IndexError, TypeError, ValueError):
                return None
        return {
            "symbol": None,  # set by caller from the request symbol
            "name": f[1] if len(f) > 1 else None,
            "price": num(_F_PRICE),
            "change": num(_F_CHANGE),
            "change_percent": num(_F_CHANGE_PCT),
            "previous_close": num(_F_PREV),
            "open": num(_F_OPEN),
            "high": num(_F_HIGH),
            "low": num(_F_LOW),
            "volume": int((num(_F_VOL) or 0) * _volume_scale(market)),
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
        codes = [tencent_symbol(s) for s in symbols]
        async with TencentClient() as client:
            rows = await client.get_quotes(codes)
        result = []
        for app_symbol, code in zip(symbols, codes):
            f = rows.get(code)
            if not f:
                continue
            snap = self._normalize_snapshot(code, f)
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
        tz = symbol_timezone(symbol)
        start = from_date or "20000101"
        end = to_date or datetime.now(_UTC).date().isoformat()
        scale = _volume_scale(market)
        async with TencentClient() as client:
            rows = await client.get_daily(tencent_symbol(symbol), start, end)
        bars = []
        for r in rows:
            if len(r) < 6:
                continue
            # Row layout: [date, open, close, high, low, vol, ...]
            bars.append(make_bar(
                to_ms(r[0], tz), r[1], r[3], r[4], r[2], r[5], volume_scale=scale,
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
            raise TencentRequestError("Tencent HK minute data unavailable")
        period = MINUTE_PERIOD.get(interval)
        if period is None:
            raise TencentRequestError(f"interval {interval!r} not supported by Tencent")
        tz = symbol_timezone(symbol)
        scale = _volume_scale(market)
        async with TencentClient() as client:
            rows = await client.get_minute(tencent_symbol(symbol), period)
        bars = []
        for r in rows:
            if len(r) < 6:
                continue
            # Minute row layout: [YYYYMMDDHHMM, open, close, high, low, vol, ...]
            bars.append(make_bar(
                minute_stamp_to_ms(str(r[0]), tz), r[1], r[3], r[4], r[2], r[5],
                volume_scale=scale,
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
TencentPriceProvider = TencentDataSource

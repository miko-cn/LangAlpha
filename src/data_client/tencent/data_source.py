"""Tencent Finance implementation of :class:`MarketDataSource` (keyless).

Free realtime + daily + A-share minute quotes for A-shares and HK. Volume
normalization is market-dependent: A-shares report lots (手, ×100 → shares),
HK reports shares directly. HK minute data is unreliable on Tencent's free
endpoints — intraday for HK raises and the chain falls through to Futu.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.data_client.cn.bars import (
    VOLUME_LOT,
    make_bar,
    minute_stamp_to_ms,
    sort_ascending,
    to_iso_date,
    to_ms,
)
from src.data_client.cn.symbols import is_cn_index, split_app_symbol, tencent_symbol
from src.data_client.market_data_provider import symbol_timezone

from .tencent_client import MINUTE_PERIOD, TencentClient, TencentRequestError

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Realtime field indices on the ``~``-split quoted string (skill-calibrated).
# A-share / ETF / index share one layout; HK reuses 1–5 / 31–34 / 39 / 44–45
# but 46+ is a different schema (don't read PB / 涨跌停 / IOPV off HK rows).
_F_PRICE, _F_PREV, _F_OPEN, _F_VOL = 3, 4, 5, 6
_F_CHANGE, _F_CHANGE_PCT, _F_HIGH, _F_LOW = 31, 32, 33, 34
_F_AMOUNT_WAN, _F_TURNOVER, _F_PE_TTM = 37, 38, 39
_F_AMPLITUDE, _F_FLOAT_MCAP_YI, _F_MCAP_YI = 43, 44, 45
_F_PB, _F_LIMIT_UP, _F_LIMIT_DOWN, _F_VOL_RATIO = 46, 47, 48, 49
_F_KIND, _F_PREMIUM_PCT, _F_IOPV = 61, 77, 78
_YI = 100_000_000.0
_WAN = 10_000.0


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
                v = float(f[i])
            except (IndexError, TypeError, ValueError):
                return None
            return v

        def pos(i: int) -> float | None:
            v = num(i)
            return v if v is not None and v > 0 else None

        price, prev = num(_F_PRICE), num(_F_PREV)
        amount = num(_F_AMOUNT_WAN)
        kind = (f[_F_KIND] if len(f) > _F_KIND else "") or ""
        row: dict[str, Any] = {
            "symbol": None,  # set by caller from the request symbol
            "name": f[1] if len(f) > 1 else None,
            "price": price,
            "change": num(_F_CHANGE),
            "change_percent": num(_F_CHANGE_PCT),
            "previous_close": prev,
            "open": num(_F_OPEN),
            "high": num(_F_HIGH),
            "low": num(_F_LOW),
            "volume": int((num(_F_VOL) or 0) * _volume_scale(market)),
            "market_status": None,
            "early_trading_change_percent": None,
            "late_trading_change_percent": None,
            "pe": pos(_F_PE_TTM),
            "market_cap": (mcap * _YI) if (mcap := pos(_F_MCAP_YI)) else None,
            "is_stale": bool(
                price and prev and price == prev and (amount is None or amount == 0)
            ),
        }
        if market == "hk":
            return row
        # A-share / ETF / index extras. 46+ on HK is a different layout.
        float_mcap = pos(_F_FLOAT_MCAP_YI)
        row.update({
            "pb": pos(_F_PB),
            "float_market_cap": float_mcap * _YI if float_mcap else None,
            "turnover_rate": pos(_F_TURNOVER),
            "amount": amount * _WAN if amount else None,
            "amplitude": pos(_F_AMPLITUDE),
            "limit_up": pos(_F_LIMIT_UP),
            "limit_down": pos(_F_LIMIT_DOWN),
            "volume_ratio": pos(_F_VOL_RATIO),
        })
        if kind.strip() in {"ETF", "LOF"}:
            iopv = pos(_F_IOPV)
            row["iopv"] = iopv
            prem = num(_F_PREMIUM_PCT)
            if prem is None and iopv and price:
                prem = (price - iopv) / iopv * 100.0
            row["premium_percent"] = prem
        return row

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
        # Empty bounds → latest 640. Mixed YYYYMMDD/ISO empties the vendor.
        start = to_iso_date(from_date) or ""
        end = to_iso_date(to_date) or ""
        qfq = "" if (is_index or is_cn_index(symbol)) else "qfq"
        scale = _volume_scale(market)
        async with TencentClient() as client:
            rows = await client.get_daily(tencent_symbol(symbol), start, end, qfq=qfq)
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

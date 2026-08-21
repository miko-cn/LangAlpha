"""Futu OpenAPI implementation of :class:`MarketDataSource`.

Futu is the authoritative A-share + HK source: daily/minutely history and
realtime snapshots (HK/US now; A-share once the account has realtime quote
permission — until then A-share snapshots raise and the chain falls through).

Prices arrive in major units and volume in shares, so no scaling is needed;
bars are stamped with Futu's ``time_key`` (Unix ms). Snapshot rows are mapped
to the unified snapshot shape used by the other sources.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config.env import FUTU_ENABLED
from src.data_client.cn.bars import to_ms
from src.data_client.cn.symbols import from_futu_symbol, futu_symbol
from src.data_client.market_data_provider import symbol_timezone
from src.utils.market_hours import current_market_phase

from .futu_client import FutuClient, FutuRequestError

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# autype: 0=unadjusted, 1=forward-adjusted (ex-dividend). Charts want adjusted.
_AUTYPE = 1


def _bar_time(row: dict[str, Any], tz: ZoneInfo) -> int:
    """Futu ``time_key`` (Unix ms); fall back to ``date`` (YYYYMMDD, local tz)."""
    ts = row.get("time_key")
    if ts:
        return int(ts)
    return to_ms(row.get("date"), tz, "%Y%m%d")


class FutuDataSource:
    """Market data source backed by the Futu OpenAPI REST (API-Key auth)."""

    @staticmethod
    def _normalize_bar(row: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
        return {
            "time": _bar_time(row, tz),
            "open": float(row.get("open") or 0.0),
            "high": float(row.get("high") or 0.0),
            "low": float(row.get("low") or 0.0),
            "close": float(row.get("close") or 0.0),
            "volume": int(row.get("volume") or 0),
        }

    @staticmethod
    def _normalize_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        price = float(row.get("last_price") or 0.0)
        prev = float(row.get("prev_close_price") or 0.0)
        change = price - prev
        pct = (change / prev * 100.0) if prev else None
        return {
            "symbol": from_futu_symbol(row.get("code", "")),
            "name": row.get("name") or row.get("sc_name"),
            "price": price,
            "change": change,
            "change_percent": pct,
            "previous_close": prev,
            "open": float(row.get("open_price") or 0.0),
            "high": float(row.get("high_price") or 0.0),
            "low": float(row.get("low_price") or 0.0),
            "volume": int(row.get("volume") or 0),
            "market_status": None,
            "early_trading_change_percent": None,
            "late_trading_change_percent": None,
        }

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ktype = FutuClient.ktype_for_interval(interval)
        tz = symbol_timezone(symbol)
        async with FutuClient() as client:
            rows = await client.get_history_kline(
                futu_symbol(symbol), ktype=ktype, autype=_AUTYPE,
                start=from_date, end=to_date,
            )
        bars = [self._normalize_bar(r, tz) for r in rows]
        return sorted((b for b in bars if b["time"] > 0), key=lambda b: b["time"])

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        tz = symbol_timezone(symbol)
        async with FutuClient() as client:
            rows = await client.get_history_kline(
                futu_symbol(symbol), ktype=2, autype=_AUTYPE,
                start=from_date, end=to_date,
            )
        bars = [self._normalize_bar(r, tz) for r in rows]
        return sorted((b for b in bars if b["time"] > 0), key=lambda b: b["time"])

    async def get_snapshots(
        self,
        symbols: list[str],
        asset_type: str = "stocks",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not FUTU_ENABLED:
            raise FutuRequestError("Futu not configured")
        codes = [futu_symbol(s) for s in symbols]
        async with FutuClient() as client:
            rows = await client.get_snapshot(codes)
        return [self._normalize_snapshot(r) for r in rows]

    async def get_market_status(
        self,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        phase = current_market_phase()
        return {
            "market": "open" if phase == "open" else ("extended-hours" if phase in ("pre", "post") else "closed"),
            "afterHours": phase == "post",
            "earlyHours": phase == "pre",
            "serverTime": datetime.now(_ET).isoformat(),
            "exchanges": None,
        }

    async def close(self) -> None:
        pass  # FutuClient manages its own lifecycle per-request


# Backward-compatible alias
FutuPriceProvider = FutuDataSource

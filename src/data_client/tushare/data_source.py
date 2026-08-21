"""Tushare implementation of :class:`MarketDataSource`.

The free tier is daily-only for A-shares (and HK on the user's token): it has
no intraday and no realtime snapshots. Unsupported capabilities raise so the
provider chain falls through to a source that has them.

Bar normalization differences from the other sources:
- ``trade_date`` is ``YYYYMMDD`` in the exchange's market timezone;
- ``vol`` is in lots (手) — scaled by ``VOLUME_LOT`` to shares to match the
  app's other sources;
- ``amount`` (成交额, 千元) is not part of the canonical bar shape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.env import TUSHARE_ENABLED
from src.data_client.base import FetchResult
from src.data_client.cn.bars import VOLUME_LOT, make_bar, sort_ascending, to_ms
from src.data_client.cn.symbols import tushare_code
from src.data_client.market_data_provider import symbol_timezone

from .tushare_client import TushareClient, TushareRequestError

logger = logging.getLogger(__name__)


def _today_compact() -> str:
    return datetime.now(timezone.utc).date().strftime("%Y%m%d")


class TushareDataSource:
    """Market data source backed by Tushare's free-tier ``daily`` interface."""

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not TUSHARE_ENABLED:
            raise TushareRequestError("Tushare not configured")
        ts_code = tushare_code(symbol)
        start = (from_date or "").replace("-", "") or "20100101"
        end = (to_date or "").replace("-", "") or _today_compact()
        tz = symbol_timezone(symbol)
        async with TushareClient() as client:
            rows = await client.get_daily(ts_code, start, end)
        bars = [
            make_bar(
                to_ms(r["trade_date"], tz, "%Y%m%d"),
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("vol"), volume_scale=VOLUME_LOT,
            )
            for r in rows if r.get("trade_date")
        ]
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
        raise TushareRequestError("Tushare free tier has no intraday data")

    async def get_snapshots(
        self,
        symbols: list[str],
        asset_type: str = "stocks",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise TushareRequestError("Tushare free tier has no realtime snapshots")

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
            "serverTime": datetime.now(timezone.utc).isoformat(),
            "exchanges": None,
        }

    async def close(self) -> None:
        pass


# Backward-compatible alias
TusharePriceProvider = TushareDataSource

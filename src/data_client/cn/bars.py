"""OHLCV bar normalization for CN/HK vendors.

Every source feeds the shared ``MarketDataSource`` contract — bars are
``{time (Unix ms), open, high, low, close, volume}``. Vendors disagree on the
time field (epoch-ms vs ``YYYY-MM-DD`` vs ``YYYYMMDD`` vs compact minute stamps)
and on volume units (shares vs lots/手); this module reconciles them.

Volume normalization: the app's other sources (FMP, yfinance) report share
counts, so CN/HK vendors that return lots (Tushare ``vol``, Tencent A-share
daily) are scaled by ``VOLUME_LOT`` so every bar's ``volume`` is shares.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Tushare ``vol`` (手) and Tencent A-share daily volume are lot counts; ×100 → shares.
VOLUME_LOT = 100

_UTC = timezone.utc


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_ms(date_str: str | int, tz: ZoneInfo, fmt: str | None = None) -> int:
    """Parse a vendor date/time string to Unix ms in exchange-local *tz*.

    Supports ``YYYY-MM-DD``, ``YYYYMMDD``, and ``YYYY-MM-DD HH:MM:SS``. Compact
    minute stamps (``YYYYMMDDHHMM``) are handled by :func:`minute_stamp_to_ms`.
    """
    s = str(date_str).strip()
    if fmt:
        dt = datetime.strptime(s, fmt).replace(tzinfo=tz)
        return int(dt.timestamp() * 1000)
    if " " in s:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    elif "-" in s:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=tz)
    else:
        dt = datetime.strptime(s, "%Y%m%d").replace(tzinfo=tz)
    return int(dt.timestamp() * 1000)


def minute_stamp_to_ms(stamp: str, tz: ZoneInfo) -> int:
    """Compact minute stamp ``YYYYMMDDHHMM`` → Unix ms in *tz* (Tencent/Sina)."""
    return to_ms(stamp, tz, "%Y%m%d%H%M")


def make_bar(time_ms: int, o: Any, h: Any, l: Any, c: Any, v: Any,
             volume_scale: float = 1.0) -> dict[str, Any]:
    """Build one canonical bar ``{time, open, high, low, close, volume}``."""
    return {
        "time": int(time_ms),
        "open": _as_float(o),
        "high": _as_float(h),
        "low": _as_float(l),
        "close": _as_float(c),
        "volume": int(_as_float(v) * volume_scale),
    }


def sort_ascending(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order bars by ``time`` ascending, dropping non-positive timestamps."""
    return sorted((b for b in bars if b["time"] > 0), key=lambda b: b["time"])

"""Pure helpers for the daily OHLCV cold store."""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.server.database.ohlcv import finalized_bars, split_head

_ET = ZoneInfo("America/New_York")
_DAY = 86_400_000
_MS = 1_750_000_000_000


def _bar(t, close=10.0):
    return {
        "time": t, "open": close, "high": close,
        "low": close, "close": close, "volume": 1,
    }


def test_split_head_tiny_series_is_all_head():
    bars = [_bar(_MS + i * _DAY) for i in range(3)]
    body, head = split_head(bars, 5)
    assert body == []
    assert len(head) == 3


def test_split_head_keeps_last_n():
    bars = [_bar(_MS + i * _DAY, float(i)) for i in range(10)]
    body, head = split_head(bars, 5)
    assert [b["close"] for b in body] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert [b["close"] for b in head] == [5.0, 6.0, 7.0, 8.0, 9.0]


def test_finalized_drops_forming_session_while_open():
    # 2026-07-01 00:00 ET
    today_open = int(datetime(2026, 7, 1, tzinfo=_ET).timestamp() * 1000)
    yesterday = today_open - _DAY
    bars = [_bar(yesterday, 1.0), _bar(today_open, 2.0)]
    kept = finalized_bars(
        bars, trading_date="2026-07-01", market_closed=False, tz=_ET,
    )
    assert [b["close"] for b in kept] == [1.0]


def test_finalized_keeps_today_when_closed():
    today_open = int(datetime(2026, 7, 1, tzinfo=_ET).timestamp() * 1000)
    bars = [_bar(today_open, 2.0)]
    kept = finalized_bars(
        bars, trading_date="2026-07-01", market_closed=True, tz=_ET,
    )
    assert [b["close"] for b in kept] == [2.0]

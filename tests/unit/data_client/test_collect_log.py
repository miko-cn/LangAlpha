"""Unit tests for the market-data collection log ring buffer."""

from __future__ import annotations

from src.data_client.collect_log import (
    _reset_for_tests,
    collect_log_snapshot,
    record_attempt,
)


def test_record_and_snapshot_roundtrip() -> None:
    _reset_for_tests()
    record_attempt(
        capability="daily", symbol="0700.HK", source="futu",
        status="ok", took_ms=12.5,
    )
    record_attempt(
        capability="snapshot", symbol="600519.SS", source="tencent",
        status="ok", took_ms=3.2,
    )
    rows = collect_log_snapshot()
    assert len(rows) == 2
    first = rows[0]
    assert first["source"] == "futu"
    assert first["status"] == "ok"
    assert first["capability"] == "daily"
    assert first["symbol"] == "0700.HK"
    assert first["took_ms"] == 12.5
    assert first["ts_ms"] > 0


def test_snapshot_returns_newest_first_capped() -> None:
    _reset_for_tests()
    for i in range(10):
        record_attempt(capability="daily", symbol=f"S{i}", source="futu",
                       status="ok", took_ms=1.0)
    rows = collect_log_snapshot(limit=3)
    # Newest 3 of the 10.
    assert len(rows) == 3
    assert rows[-1]["symbol"] == "S9"


def test_error_row_carries_detail() -> None:
    _reset_for_tests()
    record_attempt(capability="intraday", symbol="0700.HK", source="tencent",
                   status="error", took_ms=8.0, detail="connection reset")
    rows = collect_log_snapshot()
    assert rows[0]["status"] == "error"
    assert rows[0]["detail"] == "connection reset"


def test_detail_is_bounded() -> None:
    _reset_for_tests()
    record_attempt(capability="daily", symbol="X", source="futu",
                   status="error", took_ms=1.0, detail="x" * 500)
    rows = collect_log_snapshot()
    assert len(rows[0]["detail"]) <= 200


def test_ring_buffer_bounded() -> None:
    _reset_for_tests()
    for i in range(600):
        record_attempt(capability="daily", symbol=f"S{i}", source="futu",
                       status="ok", took_ms=1.0)
    assert len(collect_log_snapshot(limit=500)) == 500

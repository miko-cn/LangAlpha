"""Index daily routing: Tencent unadjusted ISO, Futu end+autype, Tushare index_daily."""

from __future__ import annotations

import pytest

from src.data_client.futu.data_source import FutuDataSource
from src.data_client.tencent.data_source import TencentDataSource
from src.data_client.tushare.data_source import TushareDataSource


class _FakeCtx:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_tencent_index_uses_unadjusted_iso_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Fake:
        async def get_daily(self, code, start, end, qfq="qfq"):
            captured.update(code=code, start=start, end=end, qfq=qfq)
            return [["2026-08-21", "3900", "3905", "3910", "3890", "1"]]

    monkeypatch.setattr(
        "src.data_client.tencent.data_source.TencentClient",
        lambda: _FakeCtx(Fake()),
    )
    bars = await TencentDataSource().get_daily(
        "000001.SS", from_date="20000101", to_date="2026-08-23",
    )
    assert captured == {
        "code": "sh000001", "start": "2000-01-01", "end": "2026-08-23", "qfq": "",
    }
    assert bars[0]["close"] == 3905.0


@pytest.mark.asyncio
async def test_tencent_stock_keeps_qfq(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Fake:
        async def get_daily(self, code, start, end, qfq="qfq"):
            captured.update(qfq=qfq, code=code)
            return []

    monkeypatch.setattr(
        "src.data_client.tencent.data_source.TencentClient",
        lambda: _FakeCtx(Fake()),
    )
    await TencentDataSource().get_daily("600519.SS")
    assert captured["qfq"] == "qfq"
    assert captured["code"] == "sh600519"


@pytest.mark.asyncio
async def test_futu_index_unadjusted(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Fake:
        async def get_history_kline(self, symbol, *, ktype, autype, start, end):
            captured.update(symbol=symbol, autype=autype, start=start, end=end, ktype=ktype)
            return [{"time_key": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]

    monkeypatch.setattr(
        "src.data_client.futu.data_source.FutuClient",
        lambda: _FakeCtx(Fake()),
    )
    await FutuDataSource().get_daily("000001.SS")
    assert captured["symbol"] == "SH.000001"
    assert captured["autype"] == 0
    assert captured["ktype"] == 2


@pytest.mark.asyncio
async def test_futu_stock_stays_qfq(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Fake:
        async def get_history_kline(self, symbol, *, ktype, autype, start, end):
            captured.update(autype=autype)
            return []

    monkeypatch.setattr(
        "src.data_client.futu.data_source.FutuClient",
        lambda: _FakeCtx(Fake()),
    )
    await FutuDataSource().get_daily("600519.SS")
    assert captured["autype"] == 1


@pytest.mark.asyncio
async def test_futu_client_unbounded_sends_end(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.data_client.futu.futu_client import FutuClient

    captured: dict = {}

    async def fake_request(self, method, path, query="", **kwargs):
        captured.update(method=method, path=path, query=query)
        return {"data": {"kline_list": []}}

    monkeypatch.setattr(FutuClient, "_request", fake_request)
    client = FutuClient(app_key="x", key_path="/tmp/unused", algo="ed25519")
    await client.get_history_kline("SH.000001", ktype=2, autype=0)
    assert "end=" in captured["query"]
    assert "start=" not in captured["query"]
    assert "num=370" in captured["query"]


@pytest.mark.asyncio
async def test_tushare_index_hits_index_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Fake:
        async def get_index_daily(self, ts_code, start, end):
            captured["api"] = "index_daily"
            captured["ts_code"] = ts_code
            return [{"trade_date": "20260821", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}]

        async def get_daily(self, ts_code, start, end):
            captured["api"] = "daily"
            return []

    monkeypatch.setenv("TUSHARE_TOKEN", "x")
    monkeypatch.setattr("src.data_client.tushare.data_source.TUSHARE_ENABLED", True)
    monkeypatch.setattr(
        "src.data_client.tushare.data_source.TushareClient",
        lambda: _FakeCtx(Fake()),
    )
    await TushareDataSource().get_daily("000001.SS")
    assert captured["api"] == "index_daily"
    assert captured["ts_code"] == "000001.SH"


@pytest.mark.asyncio
async def test_fmp_daily_raises_for_cn_index() -> None:
    from src.data_client.fmp.data_source import FMPDataSource
    from src.data_client.fmp.fmp_client import FMPRequestError

    with pytest.raises(FMPRequestError, match="A-share index"):
        await FMPDataSource().get_daily("000001.SS")


@pytest.mark.asyncio
async def test_fmp_intraday_raises_for_cn_index() -> None:
    from src.data_client.fmp.data_source import FMPDataSource
    from src.data_client.fmp.fmp_client import FMPRequestError

    with pytest.raises(FMPRequestError, match="A-share index"):
        await FMPDataSource().get_intraday("399006.SZ", "5min")


@pytest.mark.asyncio
async def test_fmp_snapshots_drop_cn_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.data_client.fmp.data_source import FMPDataSource

    captured: dict = {}

    class Fake:
        async def get_batch_quotes(self, symbols):
            captured["symbols"] = symbols
            return [{"symbol": "AAPL", "price": 1, "change": 0, "changePercentage": 0}]

    monkeypatch.setattr(
        "src.data_client.fmp.data_source.FMPClient",
        lambda: _FakeCtx(Fake()),
    )
    snaps = await FMPDataSource().get_snapshots(["000001.SS", "AAPL"])
    assert captured["symbols"] == ["AAPL"]
    assert [s["symbol"] for s in snaps] == ["AAPL"]

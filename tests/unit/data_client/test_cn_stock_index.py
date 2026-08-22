"""CN/HK suggest-search parsers and merge — no live Eastmoney/Tencent/Sina."""

from __future__ import annotations

import io
import zipfile

import pytest

from src.data_client.cn.search import (
    SearchHit,
    has_cjk,
    merge_hits,
    parse_eastmoney,
    parse_sina,
    parse_tencent,
    search,
)
from src.server.app.market_data import _merge_search_rows


def test_has_cjk() -> None:
    assert has_cjk("贵州茅台") is True
    assert has_cjk("maotai") is False
    assert has_cjk("600519") is False


def test_parse_eastmoney_keeps_ashare_and_hk_drops_bond() -> None:
    payload = {
        "QuotationCodeTable": {
            "Data": [
                {"Code": "600519", "Name": "贵州茅台", "MktNum": "1",
                 "Classify": "AStock", "SecurityTypeName": "沪A"},
                {"Code": "00700", "Name": "腾讯控股", "MktNum": "116",
                 "Classify": "HK", "SecurityTypeName": "港股"},
                {"Code": "751240", "Name": "平安银行", "MktNum": "1",
                 "Classify": "Bond", "SecurityTypeName": "债券"},
                {"Code": "TME", "Name": "腾讯音乐", "MktNum": "106",
                 "Classify": "UsStock", "SecurityTypeName": "美股"},
            ]
        }
    }
    hits = parse_eastmoney(payload)
    symbols = [h.symbol for h in hits]
    assert symbols == ["600519.SS", "0700.HK", "TME"]
    assert hits[1].currency == "HKD"
    assert hits[2].currency == "USD"


def test_parse_tencent_keeps_index_and_etf() -> None:
    text = (
        'v_hint="sh~600519~贵州茅台~gzmt~GP-A^'
        'sh~000847~腾讯济安~txja~ZS^'
        'sh~510300~沪深300ETF华泰柏瑞~hs300etf~ETF^'
        'hk~00700~腾讯控股~txkg~GP^'
        'us~tme.n~腾讯音乐~txyl~GP"'
    )
    hits = parse_tencent(text)
    assert [h.symbol for h in hits] == [
        "600519.SS", "000847.SS", "510300.SS", "0700.HK", "TME",
    ]
    assert [h.kind for h in hits] == ["stock", "index", "etf", "stock", "stock"]


def test_parse_sina_a_and_hk() -> None:
    text = (
        'var suggestvalue="贵州茅台,11,600519,sh600519,贵州茅台,,贵州茅台,99,1,ESG,,;'
        '腾讯控股,31,00700,00700,腾讯控股,,腾讯控股,99,1,ESG,,;'
        '银河基金,21,519677,of519677,银河基金,,银河基金,99,1,,,";'
    )
    hits = parse_sina(text)
    assert [h.symbol for h in hits] == ["600519.SS", "0700.HK"]


def test_merge_dedupes_and_ranks_exact_code_first() -> None:
    em = [SearchHit("002415.SZ", "海康威视", "SZ", "CNY", "eastmoney")]
    tx = [
        SearchHit("2415.HK", "other", "HK", "HKD", "tencent"),
        SearchHit("002415.SZ", "海康威视-dup", "SZ", "CNY", "tencent"),
    ]
    rows = merge_hits("2415", [em, tx], limit=10)
    assert [r["symbol"] for r in rows] == ["2415.HK", "002415.SZ"]
    assert rows[1]["name"] == "海康威视"

    hk = merge_hits("700", [[SearchHit("0700.HK", "腾讯控股", "HK", "HKD", "em")]], limit=5)
    assert hk[0]["symbol"] == "0700.HK"


def test_merge_empty_query_batches() -> None:
    assert merge_hits("x", [[], []], limit=5) == []


def test_parse_sina_keeps_sh_index_and_etf() -> None:
    text = (
        'var suggestvalue="腾讯济安,11,000847,sh000847,腾讯济安,,腾讯济安,99,1,ESG,,;'
        '沪深300ETF华泰柏瑞,22,510300,of510300,沪深300ETF华泰柏瑞,,沪深300ETF华泰柏瑞,99,1,,,;"'
    )
    hits = parse_sina(text)
    assert [h.symbol for h in hits] == ["000847.SS", "510300.SS"]
    assert [h.kind for h in hits] == ["index", "etf"]


def test_parse_eastmoney_drops_bj_legacy() -> None:
    payload = {
        "QuotationCodeTable": {
            "Data": [
                {"Code": "832982", "Name": "锦波生物", "MktNum": "90",
                 "Classify": "AStock", "SecurityTypeName": "京A"},
                {"Code": "920982", "Name": "锦波生物", "MktNum": "90",
                 "Classify": "AStock", "SecurityTypeName": "京A", "PinYin": "jbsw"},
            ]
        }
    }
    hits = parse_eastmoney(payload)
    assert [h.symbol for h in hits] == ["920982.BJ"]


def test_parse_tencent_unescapes_and_drops_warrant() -> None:
    text = (
        r'v_hint="sh~600519~\u8d35\u5dde\u8305\u53f0~gzmt~GP-A^'
        r'sz~399436~绿色煤炭~lstmt~ZS^'
        r'hk~13112~宁德时代涡轮~ndsd~GP"'
    )
    hits = parse_tencent(text)
    assert [h.symbol for h in hits] == ["600519.SS", "399436.SZ"]
    assert hits[0].name == "贵州茅台"
    assert hits[0].pinyin == "gzmt"
    assert hits[1].kind == "index"


def test_parse_eastmoney_keeps_index_and_fund() -> None:
    payload = {
        "QuotationCodeTable": {
            "Data": [
                {"Code": "000300", "Name": "沪深300", "MktNum": "1",
                 "Classify": "Index", "SecurityTypeName": "指数", "PinYin": "HS300"},
                {"Code": "510300", "Name": "沪深300ETF华泰柏瑞", "MktNum": "1",
                 "Classify": "Fund", "SecurityTypeName": "基金"},
                {"Code": "399006", "Name": "创业板指", "MktNum": "0",
                 "Classify": "Index", "SecurityTypeName": "指数"},
            ]
        }
    }
    hits = parse_eastmoney(payload)
    assert [(h.symbol, h.kind) for h in hits] == [
        ("000300.SS", "index"),
        ("510300.SS", "etf"),
        ("399006.SZ", "index"),
    ]


def test_merge_index_query_beats_etf() -> None:
    rows = merge_hits("创业板", [[
        SearchHit("159915.SZ", "创业板ETF易方达", "SZ", "CNY", "tx", kind="etf"),
        SearchHit("399006.SZ", "创业板指", "SZ", "CNY", "em", kind="index"),
    ]], limit=10)
    assert [r["symbol"] for r in rows] == ["399006.SZ", "159915.SZ"]


def test_merge_tencent_query_stock_beats_index() -> None:
    rows = merge_hits("腾讯", [[
        SearchHit("000847.SS", "腾讯济安", "SH", "CNY", "tx", kind="index"),
        SearchHit("0700.HK", "腾讯控股", "HK", "HKD", "em", kind="stock"),
    ]], limit=10)
    assert [r["symbol"] for r in rows] == ["0700.HK", "000847.SS"]


def test_merge_pinyin_exact_beats_name_substring() -> None:
    rows = merge_hits("gzmt", [[
        SearchHit("000589.SZ", "贵州轮胎", "SZ", "CNY", "tx", pinyin="gzlt"),
        SearchHit("600519.SS", "贵州茅台", "SH", "CNY", "em", pinyin="gzmt"),
    ]], limit=10)
    assert [r["symbol"] for r in rows] == ["600519.SS", "000589.SZ"]


def test_parse_tencent_empty_hint() -> None:
    assert parse_tencent('v_hint="N"') == []
    assert parse_tencent("") == []


def test_route_merge_cn_first() -> None:
    cn = [{"symbol": "600519.SS", "name": "贵州茅台"}]
    up = [{"symbol": "600519.SS", "name": "KWEICHOW MOUTAI"}, {"symbol": "AAPL", "name": "Apple"}]
    out = _merge_search_rows(cn, up, limit=10)
    assert [r["symbol"] for r in out] == ["600519.SS", "AAPL"]
    assert out[0]["name"] == "贵州茅台"


@pytest.mark.asyncio
async def test_search_empty_query() -> None:
    assert await search("") == []
    assert await search("   ") == []


@pytest.mark.asyncio
async def test_search_fans_out_and_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_em(_client, query: str, limit: int):
        return parse_eastmoney({
            "QuotationCodeTable": {
                "Data": [{
                    "Code": "600519", "Name": "贵州茅台", "MktNum": "1",
                    "Classify": "AStock", "SecurityTypeName": "沪A",
                }]
            }
        })

    async def fake_tx(_client, query: str):
        return parse_tencent('v_hint="sz~000001~平安银行~payh~GP-A"')

    async def fake_sina(_client, query: str):
        return []

    monkeypatch.setattr("src.data_client.cn.search._eastmoney", fake_em)
    monkeypatch.setattr("src.data_client.cn.search._tencent", fake_tx)
    monkeypatch.setattr("src.data_client.cn.search._sina", fake_sina)
    monkeypatch.setattr("src.data_client.cn.universe.kick_refresh", lambda: None)
    monkeypatch.setattr("src.data_client.cn.universe.match_official", lambda q, limit=50: [])

    out = await search("茅台", limit=10)
    assert [r["symbol"] for r in out] == ["600519.SS", "000001.SZ"]


@pytest.mark.asyncio
async def test_search_survives_dead_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dead(_client, *args, **kwargs):
        return []

    async def ok_tx(_client, query: str):
        return parse_tencent('v_hint="sh~600519~贵州茅台~gzmt~GP-A"')

    monkeypatch.setattr("src.data_client.cn.search._eastmoney", dead)
    monkeypatch.setattr("src.data_client.cn.search._tencent", ok_tx)
    monkeypatch.setattr("src.data_client.cn.search._sina", dead)
    monkeypatch.setattr("src.data_client.cn.universe.kick_refresh", lambda: None)
    monkeypatch.setattr("src.data_client.cn.universe.match_official", lambda q, limit=50: [])

    out = await search("maotai")
    assert [r["symbol"] for r in out] == ["600519.SS"]


def test_xlsx_inline_str_and_official_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.data_client.cn import universe as uni

    sheet = """<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>证券代码</t></is></c>
<c r="B1" t="inlineStr"><is><t>证券简称</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>159915</t></is></c>
<c r="B2" t="inlineStr"><is><t>创业板ETF易方达</t></is></c></row>
</sheetData></worksheet>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    rows = uni._xlsx_rows(buf.getvalue())
    assert rows[1] == ["159915", "创业板ETF易方达"]

    monkeypatch.setattr(uni, "_cache", [
        uni.OfficialRow("sz", "159915", "创业板ETF易方达", "etf"),
        uni.OfficialRow("sh", "600519", "贵州茅台", "stock", pinyin="gzmt"),
    ])
    assert [r.code for r in uni.match_official("创业板")] == ["159915"]
    assert [r.code for r in uni.match_official("gzmt")] == ["600519"]
    assert uni.match_official("nomatch") == []


def test_universe_disk_roundtrip_and_fresh_skip(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.data_client.cn import universe as uni

    path = tmp_path / "universe.json"
    monkeypatch.setenv("LANGALPHA_CN_UNIVERSE_PATH", str(path))
    monkeypatch.setattr(uni, "_cache", None)
    monkeypatch.setattr(uni, "_cache_at", 0.0)
    monkeypatch.setattr(uni, "_refresh_task", None)

    rows = [uni.OfficialRow("sh", "600519", "贵州茅台", "stock", "gzmt")]
    uni.write_disk(rows, datetime.now(tz=ZoneInfo("Asia/Shanghai")))
    loaded = uni.read_disk()
    assert loaded is not None
    assert [r.code for r in loaded[0]] == ["600519"]

    monkeypatch.setattr(uni, "_cache", None)
    assert [r.code for r in uni.match_official("gzmt")] == ["600519"]

    async def boom() -> list:
        raise AssertionError("fresh disk must not hit the exchanges")

    monkeypatch.setattr(uni, "fetch_official", boom)
    uni.kick_refresh()
    assert uni._refresh_task is None


def test_tencent_snapshot_etf_iopv_and_ashare_mcap() -> None:
    from src.data_client.tencent.data_source import TencentDataSource

    def row(**overrides: object) -> list[str]:
        f = [""] * 88
        f[1] = "沪深300ETF华泰柏瑞"
        f[3] = "4.680"
        f[4] = "4.653"
        f[5] = "4.648"
        f[6] = "100"
        f[31] = "0.027"
        f[32] = "0.58"
        f[33] = "4.693"
        f[34] = "4.641"
        f[37] = "340001"
        f[38] = "3.10"
        f[44] = "1098.93"
        f[45] = "1098.93"
        f[47] = "5.118"
        f[48] = "4.188"
        f[61] = "ETF"
        f[77] = "-0.01"
        f[78] = "4.6805"
        for k, v in overrides.items():
            f[int(k)] = str(v)
        return f

    etf = TencentDataSource._normalize_snapshot("sh510300", row())
    assert etf["iopv"] == pytest.approx(4.6805)
    assert etf["premium_percent"] == pytest.approx(-0.01)
    assert etf["market_cap"] == pytest.approx(1098.93 * 1e8)
    assert etf["turnover_rate"] == pytest.approx(3.10)
    assert etf["limit_up"] == pytest.approx(5.118)

    stock = TencentDataSource._normalize_snapshot("sh600519", row(
        **{"1": "贵州茅台", "3": "1272.83", "39": "19.54", "46": "6.33", "61": "GP-A", "77": "", "78": ""},
    ))
    assert stock["pe"] == pytest.approx(19.54)
    assert stock["pb"] == pytest.approx(6.33)
    assert stock.get("iopv") is None

    idx = TencentDataSource._normalize_snapshot("sh000300", row(
        **{"1": "沪深300", "3": "4618.90", "39": "14.14", "47": "-1", "61": "ZS", "77": "", "78": ""},
    ))
    assert idx["pe"] == pytest.approx(14.14)
    assert idx["limit_up"] is None
    assert idx.get("iopv") is None

    hk = TencentDataSource._normalize_snapshot("hk00700", row(
        **{"1": "腾讯控股", "3": "600", "39": "22.1", "46": "TENCENT", "61": "", "77": "9.9", "78": "1.2"},
    ))
    assert hk["pe"] == pytest.approx(22.1)
    assert "pb" not in hk
    assert "iopv" not in hk
    assert "limit_up" not in hk


def test_constituents_parse_and_alias() -> None:
    from src.data_client.cn.constituents import (
        canonicalize,
        parse_constituent_table,
        supported,
    )

    assert canonicalize("000300") == "000300.SS"
    assert canonicalize("399006") == "399006.SZ"
    assert supported("000300.SS") and supported("399300.SZ")
    assert not supported("000001.SS")

    rows = parse_constituent_table([
        ["证券代码", "证券简称"],
        ["000001", "平安银行"],
        ["600519", "贵州茅台"],
        ["300750", "宁德时代"],
    ])
    assert [(r.symbol, r.name) for r in rows] == [
        ("000001.SZ", "平安银行"),
        ("600519.SS", "贵州茅台"),
        ("300750.SZ", "宁德时代"),
    ]


def test_constituents_disk_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.data_client.cn import constituents as cons

    monkeypatch.setenv("LANGALPHA_CN_CONSTITUENTS_DIR", str(tmp_path))
    cons._mem.clear()
    cons._write_disk(
        "000300.SS",
        [cons.Constituent("000001.SZ", "平安银行")],
        datetime.now(tz=ZoneInfo("Asia/Shanghai")),
    )
    hit = cons.get_cached("000300")
    assert hit is not None
    assert [(r.symbol, r.name) for r in hit[0]] == [("000001.SZ", "平安银行")]
    assert hit[1] == "沪深300"

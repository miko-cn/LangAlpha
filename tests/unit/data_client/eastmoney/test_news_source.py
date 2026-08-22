"""Normalization + ticker mapping for the mainland news source."""

from __future__ import annotations

from src.data_client.eastmoney.client import (
    bare_a_share,
    cls_sign,
    normalize_cls,
    normalize_fast_news,
    normalize_stock_news,
)


class TestBareAShare:
    def test_yahoo_suffix(self):
        assert bare_a_share("600519.SS") == "600519"
        assert bare_a_share("000001.SZ") == "000001"
        assert bare_a_share("000001.SH") == "000001"

    def test_bare_digits(self):
        assert bare_a_share("688017") == "688017"

    def test_rejects_us_and_hk(self):
        assert bare_a_share("AAPL") is None
        assert bare_a_share("00700.HK") is None
        assert bare_a_share("HSI") is None


class TestNormalizeFastNews:
    def test_field_mapping(self):
        n = normalize_fast_news({
            "code": "202608223850220312",
            "title": "大庆华科半年报",
            "summary": "净利润同比下降",
            "showTime": "2026-08-23 00:30:49",
            "stockList": ["0.000985", "90.BK0464"],
        })
        assert n["id"] == "em-202608223850220312"
        assert n["title"] == "大庆华科半年报"
        assert n["source"]["name"] == "东方财富"
        assert n["tickers"] == ["000985.SZ"]
        assert n["published_at"].startswith("2026-08-23T00:30:49")
        assert "+08:00" in n["published_at"]
        assert n["article_url"].endswith("202608223850220312.html")


class TestNormalizeCls:
    def test_field_mapping(self):
        n = normalize_cls({
            "id": 2461493,
            "title": "叙利亚谴责以色列袭击",
            "content": "财联社8月23日电，……",
            "ctime": 1787416136,
            "subjects": [{"subject_name": "环球市场情报"}],
        })
        assert n["id"] == "cls-2461493"
        assert n["source"]["name"] == "财联社"
        assert n["article_url"] == "https://www.cls.cn/detail/2461493"
        assert n["keywords"] == ["环球市场情报"]
        assert n["published_at"].endswith("+00:00")


class TestNormalizeStockNews:
    def test_strips_html(self):
        n = normalize_stock_news(
            {"title": "<em>茅台</em>涨停", "content": "正文", "url": "https://x", "date": "2026-08-23 10:00:00"},
            "600519.SS",
        )
        assert n["title"] == "茅台涨停"
        assert n["tickers"] == ["600519.SS"]


class TestClsSign:
    def test_deterministic(self):
        params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
                  "last_time": "", "refresh_type": "1", "rn": "5"}
        assert cls_sign(params) == cls_sign(params)
        assert len(cls_sign(params)) == 32

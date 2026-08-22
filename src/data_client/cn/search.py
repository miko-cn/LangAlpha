"""Query-time CN/HK/US-Chinese stock search via public suggest APIs.

Tushare ``stock_basic`` needs a paid point grant we don't have, and Futu's
``get_search_quote`` is OpenD-only — this repo's Futu client is REST
(``webapi.futunn.com``) and has no search path. So search is a live fan-out
of three keyless suggest endpoints that already understand 中文 / 拼音缩写 /
代码:

- Eastmoney ``searchapi.eastmoney.com`` — 茅台 / gzmt / 600519 / 腾讯
- Tencent ``smartbox.gtimg.cn`` — full pinyin (maotai, pingan) + 中文
- Sina ``suggest3.sinajs.cn`` — same coverage as Tencent, different ranking

A miss on all three is a soft empty list; the search route then falls
through to FMP/yfinance for English/US queries.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.data_client.cn.symbols import app_hk_code

logger = logging.getLogger(__name__)

# Public website token, not a secret — every Eastmoney quote page embeds it.
_EM_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
_EM_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_TX_URL = "https://smartbox.gtimg.cn/s3/"
# type= lives in the path (that's the upstream's actual URL shape).
# 11=A股/指数(新浪把指数也标 11) 12=基金 22=ETF 23=LOF 31=港股。
_SINA_URL = "https://suggest3.sinajs.cn/suggest/type=11,12,22,23,31"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_EM_HEADERS = {"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"}
_SINA_HEADERS = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"}
_TX_HEADERS = {"User-Agent": _UA}

_TIMEOUT = 8.0

# Eastmoney MktNum → our market token. 105/106 are the two US boards the
# suggest API uses; 90 is 北交所; 116 is HK. Anything else is dropped.
_EM_MKT = {
    "0": "sz",
    "1": "sh",
    "90": "bj",
    "105": "us",
    "106": "us",
    "116": "hk",
}
_EM_CLASSIFY = {"AStock", "HK", "UsStock", "Index", "Fund"}
_EM_TYPE_NAME = {"沪A", "深A", "京A", "港股", "美股", "指数", "基金"}

# Tencent type: equities + 指数 + ETF/LOF. Drop 权证/场外.
_TX_KEEP_PREFIX = ("GP",)
_TX_KEEP_EXACT = {"ETF", "LOF", "ZS"}
_SINA_FUND_TYPES = {"22", "23"}

# Yahoo-style US class suffixes on Tencent codes (``tme.n``, ``tcehy.ps``).
_US_CLASS_SUFFIX = {"n", "o", "a", "b", "pk", "ob", "ps", "am"}

_HINT_RE = re.compile(r'v_hint="([^"]*)"')
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_UNICODE_ESC_RE = re.compile(r"\\u[0-9a-fA-F]{4}")

# 港股 10000–79999 是窝轮/牛熊，80000+ 是人民币柜台。
# 北交所 43/83/87 老号段已迁 920xxx，腾讯会返回定格僵尸报价。
# 000xxx.SS / 399xxx.SZ 是指数，要留（上证50 / 沪深300 / 创业板指）。
_BJ_LEGACY_PREFIX = ("43", "83", "87")


@dataclass(frozen=True)
class SearchHit:
    symbol: str
    name: str
    exchange: str  # SH / SZ / BJ / HK / US
    currency: str
    source: str
    pinyin: str = ""
    kind: str = "stock"  # stock | index | etf


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _decode(content: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _unescape_name(name: str) -> str:
    """Tencent occasionally returns literal ``\\uXXXX`` instead of CJK."""
    if not name or not _UNICODE_ESC_RE.search(name):
        return name
    try:
        return name.encode("utf-8").decode("unicode_escape")
    except UnicodeError:
        return name


def _digits(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit())


def _keep_code(market: str, code: str) -> bool:
    """Drop HK warrants and dead BJ codes. Indexes / ETFs stay."""
    digits = _digits(code)
    if market == "bj":
        return digits[:2] not in _BJ_LEGACY_PREFIX
    if market == "hk":
        if not digits:
            return False
        n = int(digits)
        return not (10000 <= n < 80000)
    return True


def market_from_code(code: str) -> str | None:
    """Infer sh/sz/bj from a 6-digit code (skill get_prefix, minus explicit suffix)."""
    digits = _digits(code)
    if not digits:
        return None
    d = digits.zfill(6)
    if d.startswith("92") or d[:2] in _BJ_LEGACY_PREFIX:
        return "bj"
    if d[0] in "569":
        return "sh"
    return "sz"


def _normalize_vendor_kind(vendor: str) -> str:
    v = (vendor or "").strip()
    low = v.lower()
    if v in {"index", "etf", "stock"}:
        return v
    if low == "zs" or "index" in low or "指数" in v:
        return "index"
    if low in {"etf", "lof", "fund"} or "基金" in v:
        return "etf"
    return ""


def _infer_kind(market: str, code: str, vendor: str = "") -> str:
    kind = _normalize_vendor_kind(vendor)
    if kind:
        return kind
    digits = _digits(code)
    if market == "sh" and digits.startswith("000"):
        return "index"
    if market == "sz" and digits.startswith("399"):
        return "index"
    if digits.startswith(("51", "56", "58", "15")):
        return "etf"
    return "stock"


def make_hit(
    market: str,
    code: str,
    name: str,
    *,
    source: str,
    vendor_kind: str = "",
    pinyin: str = "",
) -> SearchHit | None:
    name = _unescape_name((name or "").strip())
    symbol = _app_symbol(market, code)
    if not symbol or not name:
        return None
    kind = _infer_kind(market, code, vendor_kind)
    ex = _exchange(market)
    return SearchHit(
        symbol=symbol, name=name, exchange=ex,
        currency=_currency(ex), source=source,
        pinyin=(pinyin or "").strip(), kind=kind,
    )


def _app_symbol(market: str, code: str) -> str | None:
    """Vendor (market, code) → Yahoo-style app symbol. None if unusable."""
    code = (code or "").strip()
    market = (market or "").strip().lower()
    if not code or not market or not _keep_code(market, code):
        return None
    if market == "us":
        # ``tme.n`` / ``TME.O`` → TME
        base, _, suffix = code.partition(".")
        if suffix and suffix.lower() in _US_CLASS_SUFFIX:
            code = base
        return code.upper()
    if market == "hk":
        digits = _digits(code)
        if not digits:
            return None
        return f"{app_hk_code(digits)}.HK"
    if market == "sh":
        return f"{code}.SS"
    if market in {"sz", "bj"}:
        return f"{code}.{market.upper()}"
    return None


def _row(hit: SearchHit) -> dict[str, Any]:
    return {
        "symbol": hit.symbol,
        "name": hit.name,
        "currency": hit.currency,
        "stockExchange": hit.exchange,
        "exchangeShortName": hit.exchange,
        "assetType": hit.kind,
    }


def _currency(exchange: str) -> str:
    return {"HK": "HKD", "US": "USD"}.get(exchange, "CNY")


def _exchange(market: str) -> str:
    return {"sh": "SH", "sz": "SZ", "bj": "BJ", "hk": "HK", "us": "US"}[market]


# ---------------------------------------------------------------------------
# Parsers (sync, unit-tested against captured payloads)
# ---------------------------------------------------------------------------


def parse_eastmoney(payload: dict[str, Any], *, source: str = "eastmoney") -> list[SearchHit]:
    rows = (payload.get("QuotationCodeTable") or {}).get("Data") or []
    out: list[SearchHit] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        classify = str(raw.get("Classify") or "")
        type_name = str(raw.get("SecurityTypeName") or "")
        if classify not in _EM_CLASSIFY and type_name not in _EM_TYPE_NAME:
            continue
        mkt = _EM_MKT.get(str(raw.get("MktNum")))
        if type_name == "京A":
            mkt = "bj"
        if mkt is None:
            continue
        hit = make_hit(
            mkt, str(raw.get("Code") or ""), str(raw.get("Name") or ""),
            source=source, vendor_kind=classify or type_name,
            pinyin=str(raw.get("PinYin") or raw.get("Pinyin") or ""),
        )
        if hit:
            out.append(hit)
    return out


def parse_tencent(text: str, *, source: str = "tencent") -> list[SearchHit]:
    m = _HINT_RE.search(text) if text else None
    blob = m.group(1) if m else ""
    if not blob or blob in {"N", "n"}:
        return []
    out: list[SearchHit] = []
    for item in blob.split("^"):
        fields = item.split("~")
        if len(fields) < 5:
            continue
        market, code, name, _pinyin, kind = (
            fields[0].lower(), fields[1], fields[2], fields[3], fields[4],
        )
        if not (kind in _TX_KEEP_EXACT or kind.startswith(_TX_KEEP_PREFIX)):
            continue
        if market not in {"sh", "sz", "bj", "hk", "us"}:
            continue
        hit = make_hit(
            market, code, name, source=source,
            vendor_kind=kind, pinyin=_pinyin,
        )
        if hit:
            out.append(hit)
    return out


def parse_sina(text: str, *, source: str = "sina") -> list[SearchHit]:
    if "suggestvalue=" not in (text or ""):
        return []
    blob = text.split("suggestvalue=", 1)[1].strip().strip(";").strip('"')
    if not blob:
        return []
    out: list[SearchHit] = []
    for item in blob.split(";"):
        parts = item.split(",")
        if len(parts) < 5:
            continue
        typ, code, key, name = parts[1], parts[2], parts[3], parts[4]
        vendor = ""
        if typ == "31":
            market = "hk"
        elif key.startswith("sh"):
            market = "sh"
        elif key.startswith("sz"):
            market = "sz"
        elif key.startswith("bj"):
            market = "bj"
        elif typ in _SINA_FUND_TYPES:
            market = market_from_code(code)
            vendor = "etf"
        else:
            continue
        if market is None:
            continue
        hit = make_hit(market, code, name, source=source, vendor_kind=vendor)
        if hit:
            out.append(hit)
    return out


def _rank_key(query: str, hit: SearchHit, *, wants: str | None = None) -> tuple[int, int, int]:
    q = query.strip().lower()
    name_lc = hit.name.lower()
    sym_lc = hit.symbol.lower()
    bare = sym_lc.split(".", 1)[0]
    if wants == "index":
        kind_pen = 0 if hit.kind == "index" else 1
    elif wants == "etf":
        kind_pen = 0 if hit.kind == "etf" else 1
    else:
        kind_pen = {"stock": 0, "etf": 1, "index": 2}.get(hit.kind, 3)
    if q == sym_lc or q == bare:
        return (0, kind_pen, 0)
    # HK codes are stored 4-digit (0700.HK) but users type 700 / 00700.
    if hit.exchange == "HK" and bare.isdigit() and q.isdigit() and int(q) == int(bare):
        return (0, kind_pen, 0)
    if q == name_lc or (hit.pinyin and q == hit.pinyin.lower()):
        return (1, kind_pen, 0)
    if name_lc.startswith(q):
        return (2, kind_pen, 0)
    pos = name_lc.find(q)
    if pos >= 0:
        return (3, kind_pen, pos)
    if q in sym_lc:
        return (4, kind_pen, 0)
    return (5, kind_pen, 0)


def _wants_kind(query: str, hits: list[SearchHit]) -> str | None:
    q = query.strip()
    ql = q.lower()
    if "指数" in q or q.endswith("指"):
        return "index"
    if "etf" in ql or "lof" in ql or "基金" in q:
        return "etf"
    for h in hits:
        if h.kind == "index" and (h.name == q or h.name in {f"{q}指", f"{q}指数"}):
            return "index"
    return None


def merge_hits(query: str, batches: list[list[SearchHit]], *, limit: int) -> list[dict[str, Any]]:
    """Dedupe by symbol (first source wins) then rank. Eastmoney is listed first."""
    seen: set[str] = set()
    merged: list[SearchHit] = []
    for batch in batches:
        for hit in batch:
            key = hit.symbol.upper()
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    wants = _wants_kind(query, merged)
    merged.sort(key=lambda h: _rank_key(query, h, wants=wants))
    return [_row(h) for h in merged[:limit]]


# ---------------------------------------------------------------------------
# Live fetchers
# ---------------------------------------------------------------------------


async def _get(client: httpx.AsyncClient, source: str, **kwargs: Any) -> httpx.Response:
    from src.data_client._ratelimit import request_with_retry

    async def _do() -> httpx.Response:
        resp = await client.get(**kwargs)
        resp.raise_for_status()
        return resp

    return await request_with_retry(source, _do)


async def _eastmoney(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
    try:
        resp = await _get(
            client, "eastmoney",
            url=_EM_URL,
            params={"input": query, "type": 14, "token": _EM_TOKEN, "count": min(limit, 20)},
            headers=_EM_HEADERS,
        )
        return parse_eastmoney(resp.json())
    except Exception as exc:  # noqa: BLE001 — one dead source must not kill search
        logger.warning("cn.search.eastmoney_failed err=%s", exc)
        return []


async def _tencent(client: httpx.AsyncClient, query: str) -> list[SearchHit]:
    try:
        resp = await _get(
            client, "tencent",
            url=_TX_URL,
            params={"q": query, "t": "all"},
            headers=_TX_HEADERS,
        )
        return parse_tencent(_decode(resp.content))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cn.search.tencent_failed err=%s", exc)
        return []


async def _sina(client: httpx.AsyncClient, query: str) -> list[SearchHit]:
    try:
        resp = await _get(
            client, "sina",
            url=_SINA_URL,
            params={"key": query},
            headers=_SINA_HEADERS,
        )
        return parse_sina(_decode(resp.content))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cn.search.sina_failed err=%s", exc)
        return []


async def search(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Fan-out suggest APIs and return app-symbol-shaped rows.

    Empty query → []. All sources failing → [] (caller falls through).
    Official SZSE/SSE/cninfo lists merge from ``data/cn/universe.json``
    (hydrated on first call; a stale file refreshes in the background).
    """
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 100))
    from src.data_client.cn.universe import kick_refresh, match_official

    kick_refresh()
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        em, tx, sina = await asyncio.gather(
            _eastmoney(client, q, limit),
            _tencent(client, q),
            _sina(client, q),
        )
    official: list[SearchHit] = []
    for row in match_official(q, limit=limit):
        hit = make_hit(
            row.market, row.code, row.name,
            source="official", vendor_kind=row.kind, pinyin=row.pinyin,
        )
        if hit:
            official.append(hit)
    return merge_hits(q, [em, tx, sina, official], limit=limit)

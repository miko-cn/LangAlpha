"""Async clients for Eastmoney 7×24 / stock news and CLS telegraph.

Unofficial, keyless endpoints. Field layouts are fragile — treat as a
best-effort mainland feed, not a contractual API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_EM_HEADERS = {"User-Agent": _UA, "Referer": "https://kuaixun.eastmoney.com/"}
_EM_SEARCH_HEADERS = {"User-Agent": _UA, "Referer": "https://so.eastmoney.com/"}
_CLS_HEADERS = {"User-Agent": _UA, "Referer": "https://www.cls.cn/"}
_CN = ZoneInfo("Asia/Shanghai")

FAST_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
STOCK_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
CLS_ROLL_URL = "https://www.cls.cn/v1/roll/get_roll_list"

_A_SHARE_RE = re.compile(r"^(\d{6})(?:\.(SS|SZ|SH))?$", re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")


class EastmoneyRequestError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def bare_a_share(symbol: str) -> str | None:
    """``600519.SS`` / ``000001.SZ`` / ``600519`` → 6-digit code, else None."""
    m = _A_SHARE_RE.match(symbol.strip())
    return m.group(1) if m else None


def _em_stock_to_app(raw: str) -> str | None:
    """Eastmoney ``0.000985`` / ``1.600519`` → ``000985.SZ`` / ``600519.SS``."""
    if not raw or "." not in raw:
        return None
    mkt, _, code = raw.partition(".")
    if not code.isdigit() or len(code) != 6:
        return None
    if mkt == "0":
        return f"{code}.SZ"
    if mkt == "1":
        return f"{code}.SS"
    return None


def _to_iso(show_time: str) -> str:
    """``2026-08-23 00:30:49`` (CST, naive) → ISO with +08:00."""
    try:
        return (
            datetime.strptime(show_time.strip(), "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=_CN)
            .isoformat()
        )
    except (TypeError, ValueError):
        return show_time or ""


def _ctime_iso(ctime: Any) -> str:
    try:
        ts = int(ctime)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def cls_sign(params: dict[str, str]) -> str:
    """CLS v1 sign: ``md5(sha1(sorted query string))`` — no key."""
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


class EastmoneyNewsClient:
    """Fetches Eastmoney 7×24 + per-ticker CMS search."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=12.0, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_fast_news(self, limit: int = 50) -> list[dict[str, Any]]:
        client = await self._http()

        async def _do() -> list[dict[str, Any]]:
            try:
                resp = await client.get(
                    FAST_NEWS_URL,
                    params={
                        "client": "web",
                        "biz": "web_724",
                        "fastColumn": "102",
                        "sortEnd": "",
                        "pageSize": str(min(limit, 100)),
                        "req_trace": str(uuid.uuid4()),
                    },
                    headers=_EM_HEADERS,
                )
                resp.raise_for_status()
                body = resp.json()
            except httpx.HTTPStatusError as e:
                raise EastmoneyRequestError(
                    f"Eastmoney fast-news failed ({e.response.status_code})",
                    status_code=e.response.status_code,
                ) from e
            except httpx.RequestError as e:
                raise EastmoneyRequestError("Eastmoney fast-news failed") from e

            rows = (body.get("data") or {}).get("fastNewsList") or []
            return [r for r in rows if isinstance(r, dict)]

        from src.data_client._ratelimit import request_with_retry

        return await request_with_retry("eastmoney", _do)

    async def get_stock_news(self, code: str, limit: int = 20) -> list[dict[str, Any]]:
        client = await self._http()
        inner = json.dumps(
            {
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": min(limit, 50),
                        "preTag": "",
                        "postTag": "",
                    }
                },
            },
            separators=(",", ":"),
        )

        async def _do() -> list[dict[str, Any]]:
            try:
                resp = await client.get(
                    STOCK_NEWS_URL,
                    params={"cb": "jQuery_news", "param": inner},
                    headers=_EM_SEARCH_HEADERS,
                )
                resp.raise_for_status()
                text = resp.text
                json_str = text[text.index("(") + 1 : text.rindex(")")]
                body = json.loads(json_str)
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
                raise EastmoneyRequestError("Eastmoney stock-news failed") from e

            articles = (body.get("result") or {}).get("cmsArticleWebOld") or []
            return [a for a in articles if isinstance(a, dict)]

        from src.data_client._ratelimit import request_with_retry

        return await request_with_retry("eastmoney", _do)


class ClsNewsClient:
    """财联社 telegraph (v1 roll list + local sign, no key)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=12.0, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_roll(self, limit: int = 50) -> list[dict[str, Any]]:
        client = await self._http()
        params = {
            "appName": "CailianpressWeb",
            "os": "web",
            "sv": "7.7.5",
            "last_time": "",
            "refresh_type": "1",
            "rn": str(min(limit, 50)),
        }
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        url = f"{CLS_ROLL_URL}?{qs}&sign={cls_sign(params)}"

        async def _do() -> list[dict[str, Any]]:
            try:
                resp = await client.get(url, headers=_CLS_HEADERS)
                resp.raise_for_status()
                body = resp.json()
            except httpx.HTTPStatusError as e:
                raise EastmoneyRequestError(
                    f"CLS roll failed ({e.response.status_code})",
                    status_code=e.response.status_code,
                ) from e
            except httpx.RequestError as e:
                raise EastmoneyRequestError("CLS roll failed") from e

            if body.get("errno") not in (0, "0", None):
                raise EastmoneyRequestError(f"CLS roll errno={body.get('errno')}")
            rows = (body.get("data") or {}).get("roll_data") or []
            return [r for r in rows if isinstance(r, dict)]

        from src.data_client._ratelimit import request_with_retry

        return await request_with_retry("cls", _do)


def normalize_fast_news(raw: dict[str, Any]) -> dict[str, Any]:
    code = str(raw.get("code") or "")
    tickers = []
    for item in raw.get("stockList") or []:
        mapped = _em_stock_to_app(str(item))
        if mapped:
            tickers.append(mapped)
    url = f"https://finance.eastmoney.com/a/{code}.html" if code else ""
    return {
        "id": f"em-{code}" if code else "",
        "title": raw.get("title") or "",
        "author": None,
        "description": raw.get("summary") or "",
        "published_at": _to_iso(str(raw.get("showTime") or "")),
        "article_url": url,
        "image_url": None,
        "source": {
            "name": "东方财富",
            "logo_url": None,
            "homepage_url": "https://kuaixun.eastmoney.com/",
            "favicon_url": None,
        },
        "tickers": tickers,
        "keywords": [],
        "sentiments": None,
    }


def normalize_stock_news(raw: dict[str, Any], ticker: str) -> dict[str, Any]:
    url = raw.get("url") or ""
    title = _HTML_RE.sub("", raw.get("title") or "")
    body = _HTML_RE.sub("", raw.get("content") or "")[:400]
    art_id = str(raw.get("code") or raw.get("id") or url)
    return {
        "id": f"emc-{art_id}" if art_id else "",
        "title": title,
        "author": None,
        "description": body,
        "published_at": _to_iso(str(raw.get("date") or "")),
        "article_url": url,
        "image_url": None,
        "source": {
            "name": raw.get("mediaName") or "东方财富",
            "logo_url": None,
            "homepage_url": "https://so.eastmoney.com/",
            "favicon_url": None,
        },
        "tickers": [ticker],
        "keywords": [],
        "sentiments": None,
    }


def normalize_cls(raw: dict[str, Any]) -> dict[str, Any]:
    art_id = str(raw.get("id") or "")
    title = raw.get("title") or raw.get("brief") or ""
    body = raw.get("content") or raw.get("brief") or ""
    return {
        "id": f"cls-{art_id}" if art_id else "",
        "title": title,
        "author": None,
        "description": body[:400] if body else "",
        "published_at": _ctime_iso(raw.get("ctime")),
        "article_url": f"https://www.cls.cn/detail/{art_id}" if art_id else "",
        "image_url": None,
        "source": {
            "name": "财联社",
            "logo_url": None,
            "homepage_url": "https://www.cls.cn/",
            "favicon_url": None,
        },
        "tickers": [],
        "keywords": [
            s.get("subject_name")
            for s in (raw.get("subjects") or [])
            if s.get("subject_name")
        ],
        "sentiments": None,
    }

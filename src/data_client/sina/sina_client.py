"""Async client for Sina Finance's free quote endpoints (keyless).

Undocumented/reverse-engineered. Realtime (``hq.sinajs.cn``) requires a
``Referer`` header and is GBK-encoded; K-line (``quotes.sina.cn``) is JSONP
with a ``var _data=([...]);`` wrapper and supports A-shares only (HK returns
``null``). Volume is reported in shares for both.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Self

import httpx

logger = logging.getLogger(__name__)

QUOTE_URL = "https://hq.sinajs.cn/list="
KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
_REFERER = {"Referer": "https://finance.sina.com.cn/"}

# scale: 240 = daily, 5/15/30/60 = minute periods
SCALE_DAILY = 240
SCALE_MINUTE = {"5min": 5, "15min": 15, "30min": 30, "60min": 60}

_QUOTE_RE = re.compile(r'hq_str_(\w+)="([^"]*)"')
_JSONP_RE = re.compile(r"\((\[.*\])\);?\s*$", re.DOTALL)


class SinaRequestError(Exception):
    """Sina finance request failure with a sanitized message."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SinaClient:
    """Async client for hq.sinajs.cn / quotes.sina.cn."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get_quotes(self, codes: list[str]) -> dict[str, list[str]]:
        """Realtime quote rows keyed by vendor code (``sh600519`` etc.)."""
        client = await self._get_client()

        async def _do() -> dict[str, list[str]]:
            try:
                resp = await client.get(QUOTE_URL + ",".join(codes), headers=_REFERER)
                resp.raise_for_status()
                text = resp.content.decode("gbk", errors="ignore")
            except httpx.HTTPStatusError as e:
                raise SinaRequestError(f"Sina quote failed ({e.response.status_code})")
            except httpx.TimeoutException:
                raise SinaRequestError("Sina quote timed out")
            except httpx.RequestError:
                raise SinaRequestError("Sina quote failed")
            return {m.group(1): m.group(2).split(",") for m in _QUOTE_RE.finditer(text)}

        from src.data_client._ratelimit import request_with_retry
        return await request_with_retry("sina", _do)

    async def get_kline(self, symbol: str, scale: int, datalen: int = 320) -> list[dict[str, Any]]:
        """K-line rows ``[{day, open, high, low, close, volume, amount}]`` (A-shares)."""
        client = await self._get_client()
        params = {"symbol": symbol, "scale": scale, "ma": "no", "datalen": datalen}

        async def _do() -> list[dict[str, Any]]:
            try:
                resp = await client.get(KLINE_URL, params=params, headers=_REFERER)
                resp.raise_for_status()
                text = resp.text
            except httpx.HTTPStatusError as e:
                raise SinaRequestError(f"Sina kline failed ({e.response.status_code})")
            except httpx.TimeoutException:
                raise SinaRequestError("Sina kline timed out")
            except httpx.RequestError:
                raise SinaRequestError("Sina kline failed")
            m = _JSONP_RE.search(text)
            if not m:
                raise SinaRequestError("Sina kline returned an unparseable response")
            try:
                data = json.loads(m.group(1))
            except ValueError:
                raise SinaRequestError("Sina kline returned invalid JSON")
            return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

        from src.data_client._ratelimit import request_with_retry
        return await request_with_retry("sina", _do)

"""Async client for Tencent Finance's free quote endpoints (no key).

Reverse-engineered, undocumented endpoints — treat field layout as fragile.
Realtime rows are GBK-encoded ``v_<code>="...";`` strings; daily/minute klines
are JSON. Volume units differ by market: A-shares (sh/sz) report lots (手),
HK reports shares — the data source normalizes that.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

QUOTE_URL = "https://qt.gtimg.cn/q="
DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
MINUTE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"

# App interval → Tencent minute period. A-share only; HK minute is unreliable.
MINUTE_PERIOD = {"1min": "m1", "5min": "m5", "15min": "m15", "30min": "m30", "60min": "m60"}

_QUOTE_RE = re.compile(r'v_(\w+)="([^"]*)"')


class TencentRequestError(Exception):
    """Tencent finance request failure with a sanitized message."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TencentClient:
    """Async client for qt.gtimg.cn / ifzq.gtimg.cn."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "TencentClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def get_quotes(self, codes: list[str]) -> dict[str, list[str]]:
        """Realtime quote rows keyed by vendor code (``sh600519`` etc.)."""
        client = await self._get_client()
        try:
            resp = await client.get(QUOTE_URL + ",".join(codes))
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="ignore")
        except httpx.HTTPStatusError as e:
            raise TencentRequestError(f"Tencent quote failed ({e.response.status_code})")
        except httpx.TimeoutException:
            raise TencentRequestError("Tencent quote timed out")
        except httpx.RequestError:
            raise TencentRequestError("Tencent quote failed")
        return {m.group(1): m.group(2).split("~") for m in _QUOTE_RE.finditer(text)}

    async def get_daily(self, code: str, start: str, end: str, qfq: str = "qfq") -> list[list[Any]]:
        """Daily (前复权) K-line rows: ``[date, open, close, high, low, vol, ...]``."""
        client = await self._get_client()
        params = {"param": f"{code},day,{start},{end},640,{qfq}"}
        try:
            resp = await client.get(DAILY_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise TencentRequestError(f"Tencent daily failed ({e.response.status_code})")
        except httpx.TimeoutException:
            raise TencentRequestError("Tencent daily timed out")
        except (httpx.RequestError, ValueError):
            raise TencentRequestError("Tencent daily failed")
        node = (data.get("data") or {}).get(code, {})
        rows = node.get(f"{qfq}day") or node.get("day") or []
        return [list(r) for r in rows if isinstance(r, list)]

    async def get_minute(self, code: str, period: str) -> list[list[Any]]:
        """Minute K-line rows: ``[YYYYMMDDHHMM, open, close, high, low, vol, ...]``."""
        client = await self._get_client()
        params = {"param": f"{code},{period},,320"}
        try:
            resp = await client.get(MINUTE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise TencentRequestError(f"Tencent minute failed ({e.response.status_code})")
        except httpx.TimeoutException:
            raise TencentRequestError("Tencent minute timed out")
        except (httpx.RequestError, ValueError):
            raise TencentRequestError("Tencent minute failed")
        node = (data.get("data") or {}).get(code, {})
        rows = node.get(period) or []
        return [list(r) for r in rows if isinstance(r, list)]

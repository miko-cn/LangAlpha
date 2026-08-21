"""Async client for the Tushare REST API (https://api.tushare.pro).

The free tier exposes the ``daily`` interface (A-share EOD, ~50 req/min on the
basic tier); higher-points interfaces are not used here. Calls are plain JSON
POSTs with the token in the body — no signing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Self

import httpx

from src.config.env import TUSHARE_TOKEN

logger = logging.getLogger(__name__)

API_BASE = "https://api.tushare.pro"


class TushareRequestError(Exception):
    """Tushare API failure with a sanitized message (never the token)."""

    def __init__(self, message: str):
        super().__init__(message)


class TushareClient:
    """Async client for api.tushare.pro."""

    def __init__(self, token: str | None = None):
        self.token = (token or TUSHARE_TOKEN).strip()
        if not self.token:
            raise TushareRequestError("Tushare token missing — set TUSHARE_TOKEN")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(http2=True, timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

<<<<<<< HEAD
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
=======
    async def __aenter__(self) -> "TushareClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
>>>>>>> 1a7f3d62 (feat(data_client): Tushare market data source (free-tier daily))
        await self.close()

    async def pro(self, api_name: str, params: dict[str, Any],
                  fields: str = "") -> dict[str, Any]:
        """Call a Tushare interface; returns the raw ``data`` dict."""
        body = json.dumps({
            "api_name": api_name, "token": self.token,
            "params": params, "fields": fields,
        }).encode()
<<<<<<< HEAD

        async def _do() -> dict[str, Any]:
            client = await self._get_client()
            try:
                resp = await client.post(API_BASE, content=body,
                                         headers={"Content-Type": "application/json"})
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise TushareRequestError(f"Tushare API request failed ({e.response.status_code})")
            except httpx.TimeoutException:
                raise TushareRequestError("Tushare API request timed out")
            except httpx.RequestError:
                raise TushareRequestError("Tushare API request failed")
            if data.get("code") != 0:
                raise TushareRequestError(f"Tushare error: {data.get('msg')}")
            return data.get("data") or {}

        from src.data_client._ratelimit import request_with_retry
        return await request_with_retry("tushare", _do)
=======
        client = await self._get_client()
        try:
            resp = await client.post(API_BASE, content=body,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise TushareRequestError(f"Tushare API request failed ({e.response.status_code})")
        except httpx.TimeoutException:
            raise TushareRequestError("Tushare API request timed out")
        except httpx.RequestError:
            raise TushareRequestError("Tushare API request failed")
        if data.get("code") != 0:
            raise TushareRequestError(f"Tushare error: {data.get('msg')}")
        return data.get("data") or {}
>>>>>>> 1a7f3d62 (feat(data_client): Tushare market data source (free-tier daily))

    async def get_daily(self, ts_code: str, start: str, end: str) -> list[dict[str, Any]]:
        """EOD bars; ``ts_code`` like ``600519.SH``; dates ``YYYYMMDD``."""
        data = await self.pro("daily", {"ts_code": ts_code, "start_date": start, "end_date": end})
        fields = data.get("fields") or []
        return [dict(zip(fields, row)) for row in (data.get("items") or [])]

"""Async client for the Futu OpenAPI REST (traditional API-Key auth mode).

Requests are signed with the AppKey's private key (Ed25519 or RSA-SHA256) per
the official docs — signing text is the 5 fields joined by ``\\n``:
``{timestamp_ms}\\n{method}\\n{path}\\n{query_string}\\n{body_sha256_hex}``.
The signature (Base64) rides in ``Authorization`` alongside ``X-Api-Key``,
``X-Timestamp`` and ``X-Nonce``. ``str(exc)`` never embeds the URL/headers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import date, datetime, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding

from src.config.env import FUTU_APP_KEY_ID, FUTU_PRIVATE_KEY_PATH, FUTU_SIGN_ALGO

logger = logging.getLogger(__name__)

API_BASE = "https://webapi.futunn.com"

# Vendor max ``num`` is 370. Wide start→end without paging returns oldest-first
# and drops the recent window the chart actually wants — page instead.
_KLINE_PAGE = 370
_KLINE_MAX_PAGES = 8
_SH_TZ = ZoneInfo("Asia/Shanghai")

_KLINE_KTYPE = {
    "1min": 1, "3min": 10, "5min": 6, "10min": 26, "15min": 7,
    "30min": 8, "1hour": 9, "60min": 9, "2hour": 14, "120min": 14,
    "4hour": 15, "240min": 15,
}


def _kline_sort_key(row: dict[str, Any]) -> Any:
    return row.get("time_key") or row.get("date") or 0


def _kline_iso_date(row: dict[str, Any]) -> str | None:
    from src.data_client.cn.bars import to_iso_date
    return to_iso_date(str(row["date"])) if row.get("date") is not None else None


def _shift_day(iso: str, days: int) -> str:
    y, m, d = (int(p) for p in iso.split("-"))
    return (date(y, m, d) + timedelta(days=days)).isoformat()


class FutuRequestError(Exception):
    """Futu API failure with a sanitized message (never the URL/headers)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _load_private_key(path: str):
    """Load a PEM or OpenSSH private key (Ed25519 or RSA)."""
    try:
        return serialization.load_pem_private_key(open(path, "rb").read(), password=None)
    except (ValueError, TypeError):
        return serialization.load_ssh_private_key(open(path, "rb").read(), password=None)


def _sign(message: bytes, key, algo: str) -> bytes:
    if algo == "ed25519":
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise FutuRequestError("FUTU_SIGN_ALGO=ed25519 but the key is not Ed25519")
        return key.sign(message)
    if algo == "rsa-sha256":
        return key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    raise FutuRequestError(f"unknown FUTU_SIGN_ALGO: {algo}")


class FutuClient:
    """Signed HTTP client for ``webapi.futunn.com`` (async, per-request)."""

    def __init__(self, *, app_key: str | None = None, key_path: str | None = None,
                 algo: str | None = None):
        self.app_key = (app_key or FUTU_APP_KEY_ID).strip()
        self.key_path = (key_path or FUTU_PRIVATE_KEY_PATH).strip()
        self.algo = (algo or FUTU_SIGN_ALGO).strip() or "ed25519"
        if not self.app_key or not self.key_path:
            raise FutuRequestError(
                "Futu credentials missing — set FUTU_APP_KEY_ID and FUTU_PRIVATE_KEY_PATH"
            )
        self._client: httpx.AsyncClient | None = None
        self._key = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                http2=True, timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _headers(self, method: str, path: str, query: str, body: bytes) -> dict[str, str]:
        if self._key is None:
            self._key = _load_private_key(self.key_path)
        ts = int(time.time() * 1000)
        nonce = secrets.token_hex(8)
        body_part = hashlib.sha256(body).hexdigest() if body else ""
        text = f"{ts}\n{method}\n{path}\n{query}\n{body_part}".encode()
        sig = base64.b64encode(_sign(text, self._key, self.algo)).decode()
        return {
            "X-Api-Key": self.app_key,
            "X-Timestamp": str(ts),
            "X-Nonce": nonce,
            "Authorization": sig,
        }

    async def _request(
        self,
        method: str,
        path: str,
        query: str = "",
        body: bytes = b"",
        content_type: str | None = None,
    ) -> dict:
        """Single signed HTTP request, gated by the shared rate-limit bucket.

        The body-part of the signature is the SHA-256 hex of the raw body
        bytes (or empty when there is no body); a successful response is a
        JSON object whose ``ret_code`` must be ``0`` for it to be returned.
        """
        from src.data_client._ratelimit import request_with_retry

        url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
        headers = self._headers(method, path, query, body)
        if content_type:
            headers["Content-Type"] = content_type
        async def _do() -> dict:
            client = await self._get_client()
            try:
                resp = await client.request(method, url, content=body or None, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise FutuRequestError(
                    f"Futu API request failed ({e.response.status_code})",
                    status_code=e.response.status_code,
                )
            except httpx.TimeoutException:
                raise FutuRequestError("Futu API request timed out")
            except httpx.RequestError:
                raise FutuRequestError("Futu API request failed")
            if isinstance(data, dict) and data.get("ret_code") not in (0, None):
                raise FutuRequestError(
                    f"Futu API error ret_code={data.get('ret_code')} msg={data.get('ret_msg')}"
                )
            return data

        return await request_with_retry("futu", _do)

    # ------------------------------------------------------------------ quotes

    async def get_history_kline(self, symbol: str, *, ktype: int, autype: int = 0,
                                start: str | None = None, end: str | None = None,
                                num: int = _KLINE_PAGE) -> list[dict[str, Any]]:
        """Historical K-line; ``ktype`` is the Futu ktype enum (see module doc).

        ``end`` is required (``ret_code=-8`` without it). Unbounded calls
        default ``end`` to today and page backwards; a bounded ``start`` pages
        forward so a wide window does not stop on the oldest page. Same-day
        ``start==end`` omits ``start`` — that range is empty even though
        ``end`` + ``num`` has the bar. ``num`` is capped at the vendor max 370.
        """
        from src.data_client.cn.bars import to_iso_date

        end_iso = to_iso_date(end) or datetime.now(_SH_TZ).date().isoformat()
        start_iso = to_iso_date(start)
        page_num = min(num, _KLINE_PAGE)
        rows: list[dict[str, Any]] = []
        seen: set[Any] = set()

        def absorb(page: list[dict[str, Any]]) -> None:
            for row in page:
                key = row.get("time_key") or row.get("date")
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        if not start_iso:
            cursor_end = end_iso
            for _ in range(_KLINE_MAX_PAGES):
                page = await self._kline_page(
                    symbol, ktype, autype, None, cursor_end, page_num,
                )
                if not page:
                    break
                absorb(page)
                first = _kline_iso_date(min(page, key=_kline_sort_key))
                if len(page) < page_num or not first:
                    break
                prev = _shift_day(first, -1)
                if prev >= cursor_end:
                    break
                cursor_end = prev
            return rows

        cursor = start_iso
        for _ in range(_KLINE_MAX_PAGES):
            page = await self._kline_page(
                symbol, ktype, autype, cursor, end_iso, page_num,
            )
            if not page:
                break
            absorb(page)
            last = _kline_iso_date(max(page, key=_kline_sort_key))
            if len(page) < page_num or not last or last >= end_iso:
                break
            nxt = _shift_day(last, 1)
            if nxt <= cursor:
                break
            cursor = nxt
        return rows

    async def _kline_page(
        self, symbol: str, ktype: int, autype: int,
        start: str | None, end: str, num: int,
    ) -> list[dict[str, Any]]:
        q = f"ktype={ktype}&autype={autype}&num={num}&end={end}"
        if start and start != end:
            q += f"&start={start}"
        data = await self._request("GET", f"/api/v1.0/quote/{symbol}/history-kline", q)
        return (data.get("data") or {}).get("kline_list") or []

    async def get_snapshot(self, code_list: list[str]) -> list[dict[str, Any]]:
        """Realtime snapshots for up to 400 codes (``MARKET.CODE`` format)."""
        body = json.dumps({"code_list": code_list}).encode()
        data = await self._request("POST", "/api/v1.0/quote/snapshot", body=body,
                                   content_type="application/json")
        return (data.get("data") or {}).get("snapshot_list") or []

    @staticmethod
    def ktype_for_interval(interval: str) -> int:
        """Map an app interval string to a Futu ktype; raises for unsupported."""
        try:
            return _KLINE_KTYPE[interval]
        except KeyError:
            raise FutuRequestError(f"interval {interval!r} not supported by Futu")

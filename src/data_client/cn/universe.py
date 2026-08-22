"""Official CN listed-instrument universe (SZSE / SSE / cninfo).

Suggest APIs stay the interactive path. This is the completeness layer: a
full A-share + ETF + SZ-index dump, persisted to ``data/cn/universe.json``
so every worker / restart can substring-match 「银行」 without hitting the
exchanges.

Refresh budget (deliberately tiny — these hosts 403 easily):

- At most **one** live pull per 24h, and only if the on-disk file is stale
  or missing. Multi-worker: ``fcntl`` lock so only one process fetches.
- **4–6 HTTP calls** per pull, **serial** (no fan-out against the same WAF):
  cninfo stocks → SZSE ETF xlsx → SZSE index xlsx → SSE ETF (date walk ≤5).
- Token buckets: ``szse`` / ``sse`` 0.25/s burst 1; ``cninfo`` 0.5/s burst 1.

Search never awaits the pull. ``match_official`` reads memory, hydrating
from disk first; a stale file is served while a background refresh runs.
"""

from __future__ import annotations

import asyncio
import fcntl
import io
import json
import logging
import os
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TIMEOUT = 20.0
_TTL_SEC = 24 * 3600
_SCHEMA = 1
_SH_TZ = ZoneInfo("Asia/Shanghai")

_CNINFO_STOCKS = "http://www.cninfo.com.cn/new/data/szse_stock.json"
_SZSE_XLSX = "https://www.szse.cn/api/report/ShowReport"
_SSE_QUERY = "https://query.sse.com.cn/commonQuery.do"
_SSE_ETF_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"

# SSE's public index JSON sqlIds are dead; these are the ones people actually search.
# SZ indexes come from SZSE CATALOGID=1812. CSI dual-codes (399300 etc.) live there.
_SH_INDEX_SEED: tuple[tuple[str, str], ...] = (
    ("000001", "上证指数"),
    ("000016", "上证50"),
    ("000300", "沪深300"),
    ("000510", "中证A500"),
    ("000688", "科创50"),
    ("000852", "中证1000"),
    ("000903", "中证100"),
    ("000905", "中证500"),
    ("000906", "中证800"),
    ("000985", "中证全指"),
)

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XLSX_T = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"


@dataclass(frozen=True)
class OfficialRow:
    market: str
    code: str
    name: str
    kind: str  # stock | index | etf
    pinyin: str = ""


_cache: list[OfficialRow] | None = None
_cache_at = 0.0  # unix seconds of fetched_at (wall clock, survives restart)
_refresh_task: asyncio.Task[None] | None = None


def cache_path() -> Path:
    override = os.environ.get("LANGALPHA_CN_UNIVERSE_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "cn" / "universe.json"


def match_official(query: str, *, limit: int = 50) -> list[OfficialRow]:
    """Substring / pinyin / code match. Hydrates from disk; cold+no-file → []."""
    _hydrate()
    if not _cache:
        return []
    q = query.strip().lower()
    if not q:
        return []
    out: list[OfficialRow] = []
    for row in _cache:
        if (
            q in row.code.lower()
            or q in row.name.lower()
            or (row.pinyin and row.pinyin.lower().startswith(q))
        ):
            out.append(row)
            if len(out) >= limit:
                break
    return out


def kick_refresh() -> None:
    """Hydrate from disk; start a background pull only when the file is stale."""
    global _refresh_task
    _hydrate()
    if _is_fresh():
        return
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh())


def _is_fresh() -> bool:
    return bool(_cache) and (time.time() - _cache_at) < _TTL_SEC


def _hydrate() -> None:
    global _cache, _cache_at
    if _cache is not None:
        return
    loaded = read_disk()
    if loaded is None:
        return
    _cache, _cache_at = loaded
    logger.info("cn.universe.disk_hit n=%s age_h=%.1f", len(_cache), (time.time() - _cache_at) / 3600)


def read_disk() -> tuple[list[OfficialRow], float] | None:
    path = cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cn.universe.disk_unreadable err=%s", exc)
        return None
    if not isinstance(raw, dict) or raw.get("version") != _SCHEMA:
        return None
    rows = [
        OfficialRow(
            market=str(r.get("market") or ""),
            code=str(r.get("code") or ""),
            name=str(r.get("name") or ""),
            kind=str(r.get("kind") or "stock"),
            pinyin=str(r.get("pinyin") or ""),
        )
        for r in (raw.get("rows") or [])
        if isinstance(r, dict) and r.get("code") and r.get("name") and r.get("market")
    ]
    if not rows:
        return None
    try:
        fetched = datetime.fromisoformat(str(raw["fetched_at"]))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=_SH_TZ)
        at = fetched.timestamp()
    except (KeyError, TypeError, ValueError):
        at = path.stat().st_mtime
    return rows, at


def write_disk(rows: list[OfficialRow], fetched_at: datetime) -> Path:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA,
        "fetched_at": fetched_at.isoformat(),
        "count": len(rows),
        "rows": [asdict(r) for r in rows],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _try_lock() -> int | None:
    lock_path = cache_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _unlock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


async def _refresh() -> None:
    global _cache, _cache_at
    fd = _try_lock()
    if fd is None:
        _cache = None  # force re-read; the other worker may have just written
        _hydrate()
        return
    try:
        _cache = None
        _hydrate()
        if _is_fresh():
            return
        rows = await fetch_official()
        prev_n = len(_cache) if _cache else 0
        if not rows or (prev_n and len(rows) < max(1000, int(prev_n * 0.8))):
            logger.warning("cn.universe.refresh_discarded n=%s prev=%s", len(rows), prev_n)
            return
        now = datetime.now(tz=_SH_TZ)
        write_disk(rows, now)
        _cache = rows
        _cache_at = now.timestamp()
        logger.info("cn.universe.refreshed n=%s path=%s", len(rows), cache_path())
    except Exception as exc:  # noqa: BLE001
        logger.warning("cn.universe.refresh_failed err=%s", exc)
    finally:
        _unlock(fd)


async def fetch_official() -> list[OfficialRow]:
    """Live fetch, serial. Search never awaits this."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        stocks = await _cninfo_stocks(client)
        sz_etf = await _szse_xlsx(client, "1945", kind="etf", code_i=0, name_i=1)
        sz_idx = await _szse_xlsx(client, "1812", kind="index", code_i=0, name_i=1)
        sh_etf = await _sse_etfs(client)
    sh_idx = [
        OfficialRow(market="sh", code=code, name=name, kind="index")
        for code, name in _SH_INDEX_SEED
    ]
    seen: set[tuple[str, str]] = set()
    out: list[OfficialRow] = []
    for row in (*stocks, *sz_etf, *sz_idx, *sh_etf, *sh_idx):
        key = (row.market, row.code)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


async def _get(client: httpx.AsyncClient, source: str, **kwargs: Any) -> httpx.Response:
    from src.data_client._ratelimit import request_with_retry

    async def _do() -> httpx.Response:
        resp = await client.get(**kwargs)
        resp.raise_for_status()
        return resp

    return await request_with_retry(source, _do)


async def _cninfo_stocks(client: httpx.AsyncClient) -> list[OfficialRow]:
    from src.data_client.cn.search import market_from_code

    try:
        resp = await _get(
            client, "cninfo", url=_CNINFO_STOCKS,
            headers={"Referer": "http://www.cninfo.com.cn/"},
        )
        rows = (resp.json() or {}).get("stockList") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("cn.universe.cninfo_failed err=%s", exc)
        return []
    out: list[OfficialRow] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if raw.get("category") != "A股":
            continue
        code = str(raw.get("code") or "").strip()
        name = str(raw.get("zwjc") or "").strip()
        market = market_from_code(code)
        if not code or not name or market is None:
            continue
        if market == "bj" and code[:2] in ("43", "83", "87"):
            continue
        out.append(OfficialRow(
            market=market, code=code, name=name,
            kind="stock", pinyin=str(raw.get("pinyin") or ""),
        ))
    return out


async def _szse_xlsx(
    client: httpx.AsyncClient, catalog: str, *, kind: str, code_i: int, name_i: int,
) -> list[OfficialRow]:
    from src.data_client.cn.search import market_from_code

    try:
        resp = await _get(
            client, "szse",
            url=_SZSE_XLSX,
            params={"SHOWTYPE": "xlsx", "CATALOGID": catalog, "TABKEY": "tab1", "random": "0.1"},
            headers={"Referer": "https://www.szse.cn/"},
        )
        table = _xlsx_rows(resp.content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cn.universe.szse_xlsx_failed catalog=%s err=%s", catalog, exc)
        return []
    out: list[OfficialRow] = []
    for i, row in enumerate(table):
        if i == 0 or len(row) <= max(code_i, name_i):
            continue
        code = "".join(ch for ch in row[code_i] if ch.isdigit())
        name = row[name_i].strip()
        market = market_from_code(code)
        if kind == "index" and code.startswith("399"):
            market = "sz"
        if not code or not name or market is None:
            continue
        out.append(OfficialRow(market=market, code=code, name=name, kind=kind))
    return out


async def _sse_etfs(client: httpx.AsyncClient) -> list[OfficialRow]:
    today = datetime.now(tz=_SH_TZ).date()
    for delta in range(5):
        stat = (today - timedelta(days=delta)).isoformat()
        try:
            resp = await _get(
                client, "sse",
                url=_SSE_QUERY,
                params={
                    "isPagination": "true",
                    "pageHelp.pageSize": "10000",
                    "pageHelp.pageNo": "1",
                    "pageHelp.beginPage": "1",
                    "pageHelp.cacheSize": "1",
                    "pageHelp.endPage": "1",
                    "sqlId": _SSE_ETF_SQL,
                    "STAT_DATE": stat,
                },
                headers={"Referer": "https://www.sse.com.cn/"},
            )
            payload = resp.json() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("cn.universe.sse_etf_failed date=%s err=%s", stat, exc)
            continue
        rows = payload.get("result") or (payload.get("pageHelp") or {}).get("data") or []
        if not rows:
            continue
        out: list[OfficialRow] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("SEC_CODE") or "").strip()
            name = str(raw.get("SEC_NAME") or "").strip()
            if not code or not name:
                continue
            out.append(OfficialRow(market="sh", code=code, name=name, kind="etf"))
        return out
    return []


def _xlsx_rows(content: bytes) -> list[list[str]]:
    """SZSE writes ``inlineStr`` cells, not sharedStrings."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    out: list[list[str]] = []
    for row in sheet.findall("m:sheetData/m:row", _XLSX_NS):
        vals: list[str] = []
        for cell in row.findall("m:c", _XLSX_NS):
            if cell.get("t") == "inlineStr":
                is_el = cell.find("m:is", _XLSX_NS)
                vals.append(
                    "".join((t.text or "") for t in is_el.iter(_XLSX_T))
                    if is_el is not None else ""
                )
            else:
                v = cell.find("m:v", _XLSX_NS)
                vals.append(v.text or "" if v is not None else "")
        out.append(vals)
    return out

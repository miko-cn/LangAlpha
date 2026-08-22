"""Index constituents from SZSE ShowReport (CATALOGID=1747).

CSI / SSE indexes are published on SZSE under a dual code (``000300`` →
``399300``). One xlsx per index, persisted under ``data/cn/constituents/``,
same 24h + flock discipline as the universe dump. Burst-1 ``szse`` bucket
keeps two index pulls from overlapping.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from src.data_client.cn.universe import _SZSE_XLSX, _TIMEOUT, _UA, _xlsx_rows

logger = logging.getLogger(__name__)

_TTL_SEC = 24 * 3600
_SCHEMA = 1
_SH_TZ = ZoneInfo("Asia/Shanghai")

# app symbol → SZSE ZSDM. CSI dual-codes: 沪深300=399300, 中证500=399905, …
_ZSDM: dict[str, str] = {
    "000016.SS": "399016",
    "399016.SZ": "399016",
    "000300.SS": "399300",
    "399300.SZ": "399300",
    "000852.SS": "399852",
    "399852.SZ": "399852",
    "000905.SS": "399905",
    "399905.SZ": "399905",
    "399001.SZ": "399001",
    "399005.SZ": "399005",
    "399006.SZ": "399006",
    "399673.SZ": "399673",
}

_INDEX_NAME: dict[str, str] = {
    "000016.SS": "上证50",
    "399016.SZ": "上证50",
    "000300.SS": "沪深300",
    "399300.SZ": "沪深300",
    "000852.SS": "中证1000",
    "399852.SZ": "中证1000",
    "000905.SS": "中证500",
    "399905.SZ": "中证500",
    "399001.SZ": "深证成指",
    "399005.SZ": "中小100",
    "399006.SZ": "创业板指",
    "399673.SZ": "创业板50",
}


@dataclass(frozen=True)
class Constituent:
    symbol: str
    name: str


_mem: dict[str, tuple[list[Constituent], float]] = {}
_tasks: dict[str, asyncio.Task[None]] = {}


def supported(symbol: str) -> bool:
    return canonicalize(symbol) in _ZSDM


def canonicalize(symbol: str) -> str:
    s = (symbol or "").strip().upper().removeprefix("^")
    if "." in s:
        return s
    if s.startswith("399"):
        return f"{s}.SZ"
    if s.startswith("000"):
        return f"{s}.SS"
    return s


def cache_dir() -> Path:
    override = os.environ.get("LANGALPHA_CN_CONSTITUENTS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "cn" / "constituents"


def get_cached(symbol: str) -> tuple[list[Constituent], str | None] | None:
    """Memory / disk. Does not hit the exchange."""
    key = canonicalize(symbol)
    if key not in _ZSDM:
        return None
    hit = _mem.get(key)
    if hit and (time.time() - hit[1]) < _TTL_SEC:
        return hit[0], _INDEX_NAME.get(key)
    loaded = _read_disk(key)
    if loaded is None:
        return None
    rows, at = loaded
    _mem[key] = (rows, at)
    return rows, _INDEX_NAME.get(key)


def kick_refresh(symbol: str) -> None:
    key = canonicalize(symbol)
    if key not in _ZSDM:
        return
    hit = _mem.get(key)
    if hit and (time.time() - hit[1]) < _TTL_SEC:
        return
    loaded = _read_disk(key)
    if loaded and (time.time() - loaded[1]) < _TTL_SEC:
        _mem[key] = loaded
        return
    task = _tasks.get(key)
    if task is not None and not task.done():
        return
    _tasks[key] = asyncio.create_task(_refresh(key))


def _path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def _read_disk(key: str) -> tuple[list[Constituent], float] | None:
    path = _path(key)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cn.constituents.disk_unreadable key=%s err=%s", key, exc)
        return None
    if not isinstance(raw, dict) or raw.get("version") != _SCHEMA:
        return None
    rows = [
        Constituent(symbol=str(r["symbol"]), name=str(r["name"]))
        for r in (raw.get("rows") or [])
        if isinstance(r, dict) and r.get("symbol") and r.get("name")
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


def _write_disk(key: str, rows: list[Constituent], fetched_at: datetime) -> None:
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA,
        "index": key,
        "zsdm": _ZSDM[key],
        "fetched_at": fetched_at.isoformat(),
        "count": len(rows),
        "rows": [asdict(r) for r in rows],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _try_lock(key: str) -> int | None:
    lock_path = _path(key).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


async def _refresh(key: str) -> None:
    fd = _try_lock(key)
    if fd is None:
        loaded = _read_disk(key)
        if loaded:
            _mem[key] = loaded
        return
    try:
        loaded = _read_disk(key)
        if loaded and (time.time() - loaded[1]) < _TTL_SEC:
            _mem[key] = loaded
            return
        rows = await fetch_szse(_ZSDM[key])
        prev_n = len(_mem[key][0]) if key in _mem else (len(loaded[0]) if loaded else 0)
        if not rows or (prev_n and len(rows) < max(10, int(prev_n * 0.8))):
            logger.warning("cn.constituents.discarded key=%s n=%s prev=%s", key, len(rows), prev_n)
            return
        now = datetime.now(tz=_SH_TZ)
        _write_disk(key, rows, now)
        _mem[key] = (rows, now.timestamp())
        logger.info("cn.constituents.refreshed key=%s n=%s", key, len(rows))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cn.constituents.refresh_failed key=%s err=%s", key, exc)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


async def ensure(symbol: str) -> tuple[list[Constituent], str | None]:
    """Cached members, awaiting a live pull only on a cold key."""
    key = canonicalize(symbol)
    if key not in _ZSDM:
        raise KeyError(key)
    hit = get_cached(symbol)
    if hit is not None:
        at = _mem[key][1]
        if time.time() - at >= _TTL_SEC:
            kick_refresh(symbol)
        return hit
    await _refresh(key)
    hit = get_cached(symbol)
    if hit is None:
        raise RuntimeError(f"constituents unavailable for {key}")
    return hit


async def fetch_szse(zsdm: str) -> list[Constituent]:
    from src.data_client._ratelimit import request_with_retry

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA}) as client:

        async def _do() -> httpx.Response:
            resp = await client.get(
                _SZSE_XLSX,
                params={"SHOWTYPE": "xlsx", "CATALOGID": "1747", "TABKEY": "tab1", "ZSDM": zsdm, "random": "0.1"},
                headers={"Referer": "https://www.szse.cn/"},
            )
            resp.raise_for_status()
            return resp

        resp = await request_with_retry("szse", _do)

    return parse_constituent_table(_xlsx_rows(resp.content))


def parse_constituent_table(table: list[list[str]]) -> list[Constituent]:
    from src.data_client.cn.search import make_hit, market_from_code

    out: list[Constituent] = []
    seen: set[str] = set()
    for i, row in enumerate(table):
        if i == 0 or len(row) < 2:
            continue
        code = "".join(ch for ch in row[0] if ch.isdigit())
        name = row[1].replace(" ", "").strip()
        if not code or not name:
            continue
        market = market_from_code(code)
        if market is None:
            continue
        hit = make_hit(market, code, name, source="szse", vendor_kind="stock")
        if hit is None or hit.symbol in seen:
            continue
        seen.add(hit.symbol)
        out.append(Constituent(symbol=hit.symbol, name=hit.name))
    return out

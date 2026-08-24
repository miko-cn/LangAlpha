"""Postgres cold store for daily OHLCV. Redis keeps the live head.

Writes are best-effort: a closed pool (unit tests, startup race) or a
transient PG error must never fail the chart path. The advisory lock is
transaction-scoped so pooled connections cannot leak a session lock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from psycopg.rows import dict_row

from src.data_client.normalize import publisher_lineage
from src.server.database.pool import get_db_connection
from src.server.utils.pg_sanitize import strip_pg_nul_str

logger = logging.getLogger(__name__)

DAILY_SCHEMA = "ohlcv-1d"
HEAD_BARS = 5
_UPSERT_CHUNK = 500


def _bar_time(bar: dict[str, Any]) -> int:
    return int(bar.get("ts_event") or bar.get("time") or 0)


def _bar_date(bar: dict[str, Any], tz) -> str | None:
    ts = _bar_time(bar)
    if ts <= 0:
        return None
    local = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(tz)
    return local.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class OhlcvSeriesMeta:
    instrument_key: str
    schema: str
    publisher: str
    revision: int
    price_treatment: str
    watermark: int
    truncated: bool


def split_head(
    bars: list[dict[str, Any]], n: int = HEAD_BARS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Oldest-first split: ``(body, head)``. Tiny series → all head."""
    ordered = sorted(bars, key=_bar_time)
    if len(ordered) <= n:
        return [], ordered
    return ordered[:-n], ordered[-n:]


def finalized_bars(
    bars: list[dict[str, Any]],
    *,
    trading_date: str,
    market_closed: bool,
    tz,
) -> list[dict[str, Any]]:
    """Drop the forming session while the venue is still open."""
    out: list[dict[str, Any]] = []
    for bar in bars:
        day = _bar_date(bar, tz)
        if not day:
            continue
        if not market_closed and day >= trading_date:
            continue
        out.append(bar)
    return out


def _row(bar: dict[str, Any]) -> tuple[int, float, float, float, float, int] | None:
    ts = int(_bar_time(bar) or 0)
    if ts <= 0:
        return None
    try:
        vol = int(float(bar.get("volume") or 0))
    except (TypeError, ValueError):
        vol = 0
    return (
        ts,
        float(bar.get("open") or 0),
        float(bar.get("high") or 0),
        float(bar.get("low") or 0),
        float(bar.get("close") or 0),
        vol,
    )


async def load_daily_series(instrument_key: str, schema: str) -> OhlcvSeriesMeta | None:
    if schema != DAILY_SCHEMA:
        return None
    try:
        async with get_db_connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT instrument_key, schema, publisher, revision,
                           price_treatment, watermark, truncated
                    FROM ohlcv_series
                    WHERE instrument_key = %s AND schema = %s
                    """,
                    (instrument_key, schema),
                )
                row = await cur.fetchone()
    except RuntimeError:
        return None
    except Exception:
        logger.warning(
            "ohlcv.series.load_failed | key=%s", instrument_key, exc_info=True,
        )
        return None
    if not row:
        return None
    return OhlcvSeriesMeta(
        instrument_key=row["instrument_key"],
        schema=row["schema"],
        publisher=row["publisher"],
        revision=int(row["revision"]),
        price_treatment=row["price_treatment"],
        watermark=int(row["watermark"]),
        truncated=bool(row["truncated"]),
    )


async def load_daily_bars(
    instrument_key: str,
    schema: str,
    publisher: str,
    revision: int,
) -> list[dict[str, Any]]:
    if schema != DAILY_SCHEMA:
        return []
    try:
        async with get_db_connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT ts_event, open, high, low, close, volume
                    FROM ohlcv_bars
                    WHERE instrument_key = %s AND schema = %s
                      AND publisher = %s AND revision = %s
                    ORDER BY ts_event
                    """,
                    (instrument_key, schema, publisher, revision),
                )
                rows = await cur.fetchall()
    except RuntimeError:
        return []
    except Exception:
        logger.warning("ohlcv.bars.load_failed | key=%s", instrument_key, exc_info=True)
        return []
    return [
        {
            "time": int(r["ts_event"]),
            "ts_event": int(r["ts_event"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["volume"] or 0),
        }
        for r in rows
    ]


async def replace_daily_body(
    *,
    instrument_key: str,
    schema: str,
    publisher: str,
    revision: int,
    bars: list[dict[str, Any]],
    truncated: bool,
) -> None:
    """Replace the current daily body. No-op for other schemas or empty input."""
    if schema != DAILY_SCHEMA or not bars or truncated:
        return
    rows = [r for r in (_row(b) for b in bars) if r is not None]
    if not rows:
        return
    ik = strip_pg_nul_str(instrument_key) or instrument_key
    pub = strip_pg_nul_str(publisher) or publisher
    treatment = publisher_lineage(publisher)[0].value
    watermark = max(r[0] for r in rows)
    try:
        async with get_db_connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                        (ik, schema),
                    )
                    await cur.execute(
                        """
                        DELETE FROM ohlcv_bars
                        WHERE instrument_key = %s AND schema = %s
                          AND (publisher, revision) IS DISTINCT FROM (%s, %s)
                        """,
                        (ik, schema, pub, revision),
                    )
                    for i in range(0, len(rows), _UPSERT_CHUNK):
                        chunk = rows[i : i + _UPSERT_CHUNK]
                        await cur.executemany(
                            """
                            INSERT INTO ohlcv_bars (
                                instrument_key, schema, publisher, revision,
                                ts_event, open, high, low, close, volume
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (
                                instrument_key, schema, publisher, revision, ts_event
                            )
                            DO UPDATE SET
                                open = EXCLUDED.open,
                                high = EXCLUDED.high,
                                low = EXCLUDED.low,
                                close = EXCLUDED.close,
                                volume = EXCLUDED.volume
                            """,
                            [
                                (ik, schema, pub, revision, *row)
                                for row in chunk
                            ],
                        )
                    await cur.execute(
                        """
                        INSERT INTO ohlcv_series (
                            instrument_key, schema, publisher, revision,
                            price_treatment, watermark, truncated
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                        ON CONFLICT (instrument_key, schema)
                        DO UPDATE SET
                            publisher = EXCLUDED.publisher,
                            revision = EXCLUDED.revision,
                            price_treatment = EXCLUDED.price_treatment,
                            watermark = EXCLUDED.watermark,
                            truncated = FALSE,
                            updated_at = NOW()
                        """,
                        (ik, schema, pub, revision, treatment, watermark),
                    )
    except RuntimeError:
        return
    except Exception:
        logger.warning(
            "ohlcv.body.replace_failed | key=%s", instrument_key, exc_info=True,
        )

"""Ring-buffer log of market-data collection attempts.

Each provider-chain attempt (one source for one capability/symbol) appends a
row here. The Settings → Data Sources log panel reads the last N rows so an
operator can see, at a glance, which source handled a request, which fell
back, and which failed — without grepping server logs.

Process-local by design (a per-worker ring buffer, not a shared store): the
panel is a *diagnostic view of the process that served the request*, not a
liveness/truth ledger. Under `--workers N` a refresh may land on a different
worker and see that worker's slice — acceptable for a debugging surface. Do
not build control flow (fallback decisions, caching) on this data.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ROWS = 500


@dataclass
class CollectLogRow:
    """One provider-chain attempt."""

    ts_ms: int
    capability: str
    symbol: str
    source: str
    status: str  # "ok" | "empty" | "error"
    took_ms: float
    detail: str = ""


class _CollectLogBuffer:
    """Thread-safe bounded deque of collect rows (newest last)."""

    def __init__(self, maxlen: int = _MAX_ROWS) -> None:
        self._rows: deque[CollectLogRow] = deque(maxlen=maxlen)
        self._lock = Lock()

    def append(self, row: CollectLogRow) -> None:
        with self._lock:
            self._rows.append(row)

    def snapshot(self, limit: int) -> list[dict[str, Any]]:
        """Return the newest rows first, capped at ``limit``.

        Operators read the log panel top-down for "what just happened",
        so newest-first matches how the eye scans the column.
        """
        with self._lock:
            rows = list(self._rows)[-limit:]
        rows.reverse()
        return [r.__dict__ for r in rows]


_BUFFER = _CollectLogBuffer()


def record_attempt(
    *,
    capability: str,
    symbol: str,
    source: str,
    status: str,
    took_ms: float,
    detail: str = "",
) -> None:
    """Append one provider-chain attempt to the ring buffer."""
    try:
        _BUFFER.append(
            CollectLogRow(
                ts_ms=int(time.time() * 1000),
                capability=capability,
                symbol=symbol,
                source=source,
                status=status,
                took_ms=took_ms,
                detail=detail[:200],  # bound detail so a long error can't bloat the buffer
            )
        )
    except Exception:  # a logging path must never break a fetch
        logger.debug("collect_log.record_failed", exc_info=True)


def collect_log_snapshot(limit: int = 200) -> list[dict[str, Any]]:
    """Return the newest rows, oldest first, capped at ``limit``."""
    return _BUFFER.snapshot(max(1, min(limit, _MAX_ROWS)))


def _reset_for_tests() -> None:
    """Drop all rows (tests only)."""
    _BUFFER._rows.clear()  # tests reach into the deque directly

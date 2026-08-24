"""LangGraph persistence must boot even when Daytona/PTC init fails.

Regression: a missing DAYTONA_API_KEY used to abort the whole PTC try
before checkpointer init, so every Flash/PTC turn compiled a
checkpointer-less graph and the model treated each message as the first.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.app.setup import _init_langgraph_persistence, lifespan


class _FakeConn:
    async def execute(self, _sql):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakePool:
    conninfo = "postgresql://postgres:postgres@localhost:5432/postgres"

    def connection(self):
        return _FakeConn()


class _FakeSaver:
    def __init__(self, pool):
        self.conn = pool


def _persistence_patches(*, saver, store=None, same_db=True, store_error=None):
    pool = getattr(saver, "conn", None)
    store_fn = MagicMock(return_value=store)
    if store_error is not None:
        store_fn.side_effect = store_error
    return (
        patch(
            "src.server.utils.checkpointer.get_checkpointer",
            return_value=saver,
        ),
        patch(
            "src.server.utils.checkpointer.open_checkpointer_pool",
            new_callable=AsyncMock,
        ),
        patch("src.server.utils.checkpointer.get_store", store_fn),
        patch(
            "src.server.utils.checkpointer.setup_store",
            new_callable=AsyncMock,
        ),
        patch(
            "src.server.database.pool.get_db_connection_string",
            return_value=getattr(pool, "conninfo", "postgresql://app"),
        ),
        patch(
            "src.server.services.writer_guard.same_database",
            return_value=same_db,
        ),
        patch(
            "src.server.services.writer_guard.open_writer_pool",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_persistence_opens_checkpointer_store_and_writer_guard():
    saver = _FakeSaver(_FakePool())
    store = object()
    patches = _persistence_patches(saver=saver, store=store)
    with patches[0] as get_cp, patches[1] as open_pool, patches[2], patches[
        3
    ] as setup_store, patches[4], patches[5], patches[6] as open_guard:
        got_saver, got_store = await _init_langgraph_persistence()

    assert got_saver is saver
    assert got_store is store
    get_cp.assert_called_once()
    open_pool.assert_awaited_once_with(saver)
    setup_store.assert_awaited_once_with(store)
    open_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_persistence_raises_when_checkpointer_missing():
    patches = _persistence_patches(saver=object())
    with (
        patch("src.server.utils.checkpointer.get_checkpointer", return_value=None),
        patches[1],
    ):
        with pytest.raises(RuntimeError, match="checkpointer failed to initialize"):
            await _init_langgraph_persistence()


@pytest.mark.asyncio
async def test_persistence_raises_when_pool_health_check_fails():
    class _DeadConn(_FakeConn):
        async def execute(self, _sql):
            raise OSError("connection refused")

    class _DeadPool(_FakePool):
        def connection(self):
            return _DeadConn()

    saver = _FakeSaver(_DeadPool())
    patches = _persistence_patches(saver=saver)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with pytest.raises(OSError, match="connection refused"):
            await _init_langgraph_persistence()


@pytest.mark.asyncio
async def test_store_failure_is_nonfatal():
    saver = _FakeSaver(_FakePool())
    patches = _persistence_patches(
        saver=saver, store_error=RuntimeError("store table missing")
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        got_saver, got_store = await _init_langgraph_persistence()

    assert got_saver is saver
    assert got_store is None


def test_lifespan_inits_persistence_outside_ptc_try():
    """Missing Daytona must not skip checkpointer init (source-order lock)."""
    src = inspect.getsource(lifespan)
    persist_at = src.index("await _init_langgraph_persistence()")
    ptc_try_at = src.index("Loading PTC Agent configuration")
    assert persist_at < ptc_try_at
    assert "get_checkpointer(" not in src

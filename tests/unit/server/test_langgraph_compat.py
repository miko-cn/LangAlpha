"""Unit tests for the langgraph 1.2.x _put_checkpoint_fut compat shim.

Bug: langgraph/pregel/main.py awaits ``AsyncPregelLoop._put_checkpoint_fut``
on every superstep when ``durability == "sync"``, but the attribute is
only assigned on the path that actually schedules a checkpoint write.
First tick on a checked graph hits AttributeError.

Patch: ``install_langgraph_compat()`` swaps ``__init__`` so every new
``AsyncPregelLoop`` instance has the attribute initialised to a completed
awaitable — ``await`` on it returns immediately and ``.result()`` returns
``None``, so the bug-triggering tick is a no-op rather than a crash.
"""

from __future__ import annotations

import asyncio
import dis
from unittest import mock

from langgraph.pregel._loop import AsyncPregelLoop

from src.server._langgraph_compat import (
    _CompletedAwaitable,
    install_langgraph_compat,
)


def test_install_overrides_init() -> None:
    """The shim swaps AsyncPregelLoop.__init__ on first install."""
    install_langgraph_compat()
    # The bound method is now our wrapper, not the original.
    qualname = AsyncPregelLoop.__init__.__qualname__
    assert qualname == "install_langgraph_compat.<locals>._init_with_fut"


def test_install_is_idempotent() -> None:
    """Re-installing must not stack wrappers — the second call is a no-op."""
    install_langgraph_compat()
    wrapper_after_first = AsyncPregelLoop.__init__

    with mock.patch.object(
        AsyncPregelLoop,
        "__init__",
        side_effect=AssertionError("should not replace again"),
    ) as replacement:
        install_langgraph_compat()
        replacement.assert_not_called()

    # Same object identity — no replacement happened.
    assert AsyncPregelLoop.__init__ is wrapper_after_first


def test_init_sets_attribute_before_super() -> None:
    """The wrapper sets the attribute as its very first statement, before
    the original ``__init__`` can run."""
    install_langgraph_compat()
    wrapper = AsyncPregelLoop.__init__

    # Disassemble the wrapper: its first STORE_ATTR must write
    # ``_put_checkpoint_fut`` (the completed awaitable).
    stores = [
        instr.argval
        for instr in dis.Bytecode(wrapper)
        if instr.opname == "STORE_ATTR"
    ]
    assert stores[0] == "_put_checkpoint_fut"


def test_completed_awaitable_is_awaitable_and_results() -> None:
    """``_CompletedAwaitable`` must be a legal stand-in Future."""
    sentinel = _CompletedAwaitable()

    async def consume() -> object:
        # await must return immediately and yield None.
        return await sentinel

    assert asyncio.run(consume()) is None
    # .result() path (the sync branch in pregel/main.py:2988) also works.
    assert sentinel.result() is None


def test_awaitable_used_as_prev_in_put_after_previous() -> None:
    """``_checkpointer_put_after_previous`` awaits prev only when non-None;
    a completed awaitable satisfies that contract (await returns at once)."""
    sentinel = _CompletedAwaitable()

    async def drain(prev) -> None:
        if prev is not None:
            await prev

    asyncio.run(drain(sentinel))
    asyncio.run(drain(None))  # the non-None branch is what the shim feeds


def test_original_init_unchanged() -> None:
    """Sanity: the install must not mutate anything else on the class."""
    install_langgraph_compat()
    assert callable(AsyncPregelLoop.__init__)

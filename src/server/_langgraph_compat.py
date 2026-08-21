"""Compat shim for langgraph 1.2 internals.

langgraph 1.2.x's ``pregel/main.py`` awaits ``AsyncPregelLoop._put_checkpoint_fut``
unconditionally on every superstep when ``durability == "sync"``, but the
attribute is only assigned on the path where ``do_checkpoint`` and
``_checkpointer_put_after_previous`` are both truthy. When the graph has a
checkpointer but the current tick skips the checkpoint path (or has not yet
reached one), the attribute is missing and the run dies with::

    AttributeError: 'AsyncPregelLoop' object has no attribute '_put_checkpoint_fut'.
    Did you mean: '_put_checkpoint'?

This module installs an ``__init__`` patch that initialises the attribute to
a *completed* awaitable. ``await`` on it returns immediately (no pending
checkpoint to wait for), ``.result()`` returns ``None``, and it is a legal
``prev`` value for ``_checkpointer_put_after_previous`` (which awaits it only
when non-None). Idempotent — safe to import multiple times.

The shim is loaded explicitly from ``src/server/app/setup.py`` before any
graph is built, alongside the OTel class-level patches.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCHED = False


class _CompletedAwaitable:
    """A stand-in Future that is always done.

    ``await`` returns immediately and ``result()`` returns ``None``. Used as
    the initial ``_put_checkpoint_fut`` so the unconditional ``await`` in
    ``pregel/main.py`` is a no-op when no checkpoint write is pending.
    """

    __slots__ = ()

    def __await__(self):
        if False:  # pragma: no cover - makes this a generator
            yield None
        return None

    def result(self) -> None:
        return None


def install_langgraph_compat() -> None:
    """Initialise ``_put_checkpoint_fut`` on every ``AsyncPregelLoop`` instance.

    Idempotent: re-importing is a no-op. We patch ``__init__`` rather than the
    callsite because the public API has no setter and the callsite runs
    before any user code can intervene.
    """
    global _PATCHED
    if _PATCHED:
        return

    from langgraph.pregel._loop import AsyncPregelLoop

    original_init = AsyncPregelLoop.__init__

    def _init_with_fut(self, *args, **kwargs):
        # Completed awaitable: awaiting it returns immediately, so a tick
        # that reaches the sync-durability wait with no pending checkpoint
        # write is a no-op instead of an AttributeError.
        self._put_checkpoint_fut = _CompletedAwaitable()
        original_init(self, *args, **kwargs)

    AsyncPregelLoop.__init__ = _init_with_fut
    _PATCHED = True
    logger.info(
        "Applied langgraph compat patch: AsyncPregelLoop.__init__ "
        "initialises _put_checkpoint_fut to a completed awaitable"
    )
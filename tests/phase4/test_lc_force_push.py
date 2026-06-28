"""Phase 4 STEP 4 — LC force_push tests.

Covers:
- force_push marks trace_id on LockManager
- subsequent acquire for that trace_id raises CancelledError
- other trace_ids are not affected
- force_push is idempotent
- force_push with no lock_manager is a no-op (legacy path)
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.lock_manager import LockManager, LockType
from app.routing.lc import LocusCoeruleus


def make_lc_with_lm() -> tuple[LocusCoeruleus, LockManager]:
    lm = LockManager()
    lc = LocusCoeruleus(lock_manager=lm)
    return lc, lm


# ---------------------------------------------------------------------------
# force_push marks the trace_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_push_marks_trace_in_lock_manager():
    lc, lm = make_lc_with_lm()
    assert not lm.is_force_pushed("t1")
    await lc.force_push("t1")
    assert lm.is_force_pushed("t1")


@pytest.mark.asyncio
async def test_force_push_blocks_subsequent_acquire():
    lc, lm = make_lc_with_lm()
    await lc.force_push("t1")

    with pytest.raises(asyncio.CancelledError):
        async with lm.acquire("t1", "context_agent_result", LockType.FIELD_UPDATE):
            pass


@pytest.mark.asyncio
async def test_force_push_does_not_affect_other_trace_ids():
    lc, lm = make_lc_with_lm()
    await lc.force_push("t1")

    # t2 must still acquire without error.
    acquired = False
    async with lm.acquire("t2", "context_agent_result", LockType.FIELD_UPDATE):
        acquired = True
    assert acquired


@pytest.mark.asyncio
async def test_force_push_is_idempotent():
    lc, lm = make_lc_with_lm()
    await lc.force_push("t1")
    await lc.force_push("t1")  # second call must not raise
    assert lm.is_force_pushed("t1")


# ---------------------------------------------------------------------------
# No lock_manager injected → no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_push_noop_when_no_lock_manager():
    lc = LocusCoeruleus()  # no lock_manager
    # Must complete without any error.
    await lc.force_push("t1")

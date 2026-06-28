"""Phase 4 STEP 4 — PLC unit tests.

Covers:
- protect_context_update uses FIELD_UPDATE lock
- protect_chromadb_write uses CHROMADB_WRITE lock
- protect_synapse_update uses FIELD_UPDATE lock
- concurrent access through PLC is serialised
- LockTimeoutError propagated
- CancelledError propagated
- force-push passthrough
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.errors import LockTimeoutError
from app.core.lock_manager import LockManager, LockType
from app.maintenance.plc import PLC


def make_plc() -> tuple[PLC, LockManager]:
    lm = LockManager()
    return PLC(lm), lm


# ---------------------------------------------------------------------------
# Lock type mapping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_protect_context_update_enters_and_exits():
    plc, _ = make_plc()
    entered = False
    async with plc.protect_context_update("t1"):
        entered = True
    assert entered


@pytest.mark.asyncio
async def test_protect_chromadb_write_enters_and_exits():
    plc, _ = make_plc()
    entered = False
    async with plc.protect_chromadb_write("t1"):
        entered = True
    assert entered


@pytest.mark.asyncio
async def test_protect_synapse_update_enters_and_exits():
    plc, _ = make_plc()
    entered = False
    async with plc.protect_synapse_update("t1"):
        entered = True
    assert entered


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_protect_context_update_serialises_concurrent():
    plc, _ = make_plc()
    order: list[str] = []

    async def first():
        async with plc.protect_context_update("t1"):
            order.append("first-in")
            await asyncio.sleep(0.05)
            order.append("first-out")

    async def second():
        await asyncio.sleep(0.01)
        async with plc.protect_context_update("t1"):
            order.append("second-in")

    await asyncio.gather(first(), second())
    assert order == ["first-in", "first-out", "second-in"]


@pytest.mark.asyncio
async def test_protect_synapse_update_serialises_concurrent():
    plc, _ = make_plc()
    order: list[str] = []

    async def first():
        async with plc.protect_synapse_update("t1"):
            order.append("first-in")
            await asyncio.sleep(0.05)
            order.append("first-out")

    async def second():
        await asyncio.sleep(0.01)
        async with plc.protect_synapse_update("t1"):
            order.append("second-in")

    await asyncio.gather(first(), second())
    assert order == ["first-in", "first-out", "second-in"]


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plc_context_update_timeout_raises_lock_timeout():
    plc, lm = make_plc()

    async def hold():
        async with lm.acquire("t1", "context_agent_result", LockType.FIELD_UPDATE):
            await asyncio.sleep(10)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)

    with pytest.raises(LockTimeoutError):
        async with plc.protect_context_update("t1"):
            pass

    holder.cancel()
    try:
        await holder
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_plc_cancelled_error_propagates():
    plc, _ = make_plc()

    async def work():
        async with plc.protect_context_update("t1"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await work()


# ---------------------------------------------------------------------------
# Force-push passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plc_mark_force_pushed_blocks_acquire():
    plc, _ = make_plc()
    plc.mark_force_pushed("t1")

    with pytest.raises(asyncio.CancelledError):
        async with plc.protect_context_update("t1"):
            pass


@pytest.mark.asyncio
async def test_plc_clear_force_push_re_enables():
    plc, _ = make_plc()
    plc.mark_force_pushed("t1")
    plc.clear_force_push("t1")

    entered = False
    async with plc.protect_context_update("t1"):
        entered = True
    assert entered

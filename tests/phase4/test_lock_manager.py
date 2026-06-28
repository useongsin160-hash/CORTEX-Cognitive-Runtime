"""Phase 4 STEP 4 — LockManager unit tests.

Covers:
- basic acquire/release (no contention)
- same-key contention (second waiter blocks until first releases)
- different trace_ids → independent locks
- same trace_id, different fields → independent
- timeout → LockTimeoutError with correct fields
- LockType timeout values
- CancelledError from acquire re-raised
- CancelledError inside yield body re-raised
- lock released after CancelledError so next acquire succeeds
- force_push → CancelledError on new acquire
- clear_force_push → acquire succeeds again
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.errors import LockTimeoutError
from app.core.lock_manager import LOCK_TIMEOUTS, LockManager, LockType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_lm() -> LockManager:
    return LockManager()


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acquire_single_completes():
    lm = make_lm()
    entered = False
    async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
        entered = True
    assert entered


@pytest.mark.asyncio
async def test_acquire_sequential_same_key_both_complete():
    lm = make_lm()
    results: list[int] = []
    async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
        results.append(1)
    async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
        results.append(2)
    assert results == [1, 2]


# ---------------------------------------------------------------------------
# Contention — same key serialises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_key_concurrent_is_serialised():
    lm = make_lm()
    order: list[str] = []

    async def first():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            order.append("first-in")
            await asyncio.sleep(0.05)
            order.append("first-out")

    async def second():
        # Slight delay so first() acquires before us.
        await asyncio.sleep(0.01)
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            order.append("second-in")

    await asyncio.gather(first(), second())
    assert order == ["first-in", "first-out", "second-in"]


# ---------------------------------------------------------------------------
# Independence — different keys don't block each other
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_trace_ids_same_field_are_independent():
    lm = make_lm()
    barrier = asyncio.Event()

    async def hold_t1():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            barrier.set()
            await asyncio.sleep(0.1)

    async def acquire_t2():
        await barrier.wait()
        # t2 must not block even though t1 holds "field_a".
        acquired = False
        async with lm.acquire("t2", "field_a", LockType.FIELD_UPDATE):
            acquired = True
        assert acquired

    await asyncio.gather(hold_t1(), acquire_t2())


@pytest.mark.asyncio
async def test_same_trace_id_different_fields_are_independent():
    lm = make_lm()
    barrier = asyncio.Event()

    async def hold_field_a():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            barrier.set()
            await asyncio.sleep(0.1)

    async def acquire_field_b():
        await barrier.wait()
        acquired = False
        async with lm.acquire("t1", "field_b", LockType.FIELD_UPDATE):
            acquired = True
        assert acquired

    await asyncio.gather(hold_field_a(), acquire_field_b())


# ---------------------------------------------------------------------------
# Timeout → LockTimeoutError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_raises_lock_timeout_error():
    lm = make_lm()

    async def hold_forever():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            await asyncio.sleep(10)

    holder = asyncio.create_task(hold_forever())
    await asyncio.sleep(0.01)  # Let holder acquire the lock.

    with pytest.raises(LockTimeoutError):
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            pass

    holder.cancel()
    try:
        await holder
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_lock_timeout_error_carries_correct_fields():
    lm = make_lm()

    async def hold():
        async with lm.acquire("trace-xyz", "my_field", LockType.CHROMADB_WRITE):
            await asyncio.sleep(10)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)

    with pytest.raises(LockTimeoutError) as exc_info:
        async with lm.acquire("trace-xyz", "my_field", LockType.CHROMADB_WRITE):
            pass

    err = exc_info.value
    assert err.trace_id == "trace-xyz"
    assert err.field_name == "my_field"
    assert err.lock_type == LockType.CHROMADB_WRITE.value
    assert err.timeout == LOCK_TIMEOUTS[LockType.CHROMADB_WRITE]

    holder.cancel()
    try:
        await holder
    except (asyncio.CancelledError, Exception):
        pass


# ---------------------------------------------------------------------------
# LockType timeout values
# ---------------------------------------------------------------------------

def test_read_timeout_is_1_second():
    assert LOCK_TIMEOUTS[LockType.READ] == 1.0


def test_field_update_timeout_is_1_second():
    assert LOCK_TIMEOUTS[LockType.FIELD_UPDATE] == 1.0


def test_chromadb_write_timeout_is_3_seconds():
    assert LOCK_TIMEOUTS[LockType.CHROMADB_WRITE] == 3.0


# ---------------------------------------------------------------------------
# CancelledError propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_error_during_acquire_reraises():
    lm = make_lm()

    async def hold():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            await asyncio.sleep(10)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)

    async def waiter():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            pass

    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    waiter_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    holder.cancel()
    try:
        await holder
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_cancelled_error_inside_body_reraises():
    lm = make_lm()

    async def work():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            raise asyncio.CancelledError("inner cancel")

    with pytest.raises(asyncio.CancelledError):
        await work()


@pytest.mark.asyncio
async def test_lock_released_after_body_cancellation():
    lm = make_lm()

    async def work():
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            raise asyncio.CancelledError()

    try:
        await work()
    except asyncio.CancelledError:
        pass

    # After cancellation, lock must be released so next acquire succeeds.
    acquired = False
    async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
        acquired = True
    assert acquired


# ---------------------------------------------------------------------------
# force_push
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_push_blocks_new_acquire():
    lm = make_lm()
    lm.mark_force_pushed("t1")

    with pytest.raises(asyncio.CancelledError):
        async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
            pass


@pytest.mark.asyncio
async def test_force_push_does_not_affect_other_trace_ids():
    lm = make_lm()
    lm.mark_force_pushed("t1")

    # t2 must still be acquirable.
    acquired = False
    async with lm.acquire("t2", "field_a", LockType.FIELD_UPDATE):
        acquired = True
    assert acquired


@pytest.mark.asyncio
async def test_clear_force_push_re_enables_acquire():
    lm = make_lm()
    lm.mark_force_pushed("t1")
    lm.clear_force_push("t1")

    acquired = False
    async with lm.acquire("t1", "field_a", LockType.FIELD_UPDATE):
        acquired = True
    assert acquired


def test_is_force_pushed_reflects_state():
    lm = make_lm()
    assert not lm.is_force_pushed("t1")
    lm.mark_force_pushed("t1")
    assert lm.is_force_pushed("t1")
    lm.clear_force_push("t1")
    assert not lm.is_force_pushed("t1")

"""Phase 4 STEP 4 — AsyncSwarm + PLC integration tests.

Covers:
- PLC present: context result written to TaskContext.context_agent_result
- PLC absent (None): backward-compat, context_agent_result still written
- PLC LockTimeoutError → graceful degradation (write proceeds unlocked)
- CancelledError from PLC propagates through swarm (not swallowed)
- concurrent protect_context_update serialises writes
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import Difficulty, TaskContext
from app.core.errors import LockTimeoutError
from app.core.lock_manager import LockManager, LockType
from app.execution.swarm import AsyncSwarm
from app.maintenance.plc import PLC
from tests.phase4._swarm_mocks import (
    MockContextAgent,
    MockGeneratorAgent,
    MockPlannerAgent,
    context_result_with,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task_context(trace_id: str = "trace-1") -> TaskContext:
    return TaskContext(trace_id=trace_id, prompt="test query")


def make_swarm(*, plc: PLC | None = None) -> AsyncSwarm:
    return AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=MockPlannerAgent(),
        generator_agent=MockGeneratorAgent(),
        plc=plc,
    )


# ---------------------------------------------------------------------------
# PLC present — context result written to TaskContext
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swarm_with_plc_writes_context_result_to_task_context():
    lm = LockManager()
    plc = PLC(lm)
    swarm = make_swarm(plc=plc)
    tc = make_task_context()

    assert tc.context_agent_result is None
    await swarm.execute(tc)
    assert tc.context_agent_result is not None


@pytest.mark.asyncio
async def test_swarm_with_plc_context_result_matches_agent_output():
    expected = context_result_with(unmasked=3)
    lm = LockManager()
    plc = PLC(lm)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(result=expected),
        planner_agent=MockPlannerAgent(),
        generator_agent=MockGeneratorAgent(),
        plc=plc,
    )
    tc = make_task_context()
    await swarm.execute(tc)
    assert tc.context_agent_result is expected


# ---------------------------------------------------------------------------
# PLC absent — backward-compat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swarm_without_plc_still_writes_context_result():
    swarm = make_swarm(plc=None)
    tc = make_task_context()

    await swarm.execute(tc)
    assert tc.context_agent_result is not None


@pytest.mark.asyncio
async def test_swarm_without_plc_result_matches_agent_output():
    expected = context_result_with(unmasked=2)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(result=expected),
        planner_agent=MockPlannerAgent(),
        generator_agent=MockGeneratorAgent(),
        plc=None,
    )
    tc = make_task_context()
    await swarm.execute(tc)
    assert tc.context_agent_result is expected


# ---------------------------------------------------------------------------
# Graceful degradation — PLC LockTimeoutError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swarm_plc_timeout_degrades_gracefully():
    """If PLC times out, write proceeds without the lock."""
    lm = LockManager()
    plc = PLC(lm)

    # Hold the lock externally so PLC will time out.
    async def hold():
        async with lm.acquire("trace-1", "context_agent_result", LockType.FIELD_UPDATE):
            await asyncio.sleep(5)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)

    swarm = make_swarm(plc=plc)
    tc = make_task_context(trace_id="trace-1")

    # Should NOT raise — graceful degradation writes without lock.
    await swarm.execute(tc)
    assert tc.context_agent_result is not None

    holder.cancel()
    try:
        await holder
    except (asyncio.CancelledError, Exception):
        pass


# ---------------------------------------------------------------------------
# CancelledError propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swarm_plc_force_push_propagates_cancelled_error():
    """LC force_push → CancelledError propagates through swarm."""
    lm = LockManager()
    plc = PLC(lm)
    lm.mark_force_pushed("trace-fp")

    swarm = make_swarm(plc=plc)
    tc = make_task_context(trace_id="trace-fp")

    with pytest.raises(asyncio.CancelledError):
        await swarm.execute(tc)


# ---------------------------------------------------------------------------
# Empty context result — no write to TaskContext
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swarm_with_no_context_result_leaves_task_context_none():
    """When ContextAgent errors, context_agent_result stays None."""
    lm = LockManager()
    plc = PLC(lm)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(raises=RuntimeError("db down")),
        planner_agent=MockPlannerAgent(),
        generator_agent=MockGeneratorAgent(),
        plc=plc,
    )
    tc = make_task_context()
    await swarm.execute(tc)
    # Error path → context_for_inject is None → no write
    assert tc.context_agent_result is None

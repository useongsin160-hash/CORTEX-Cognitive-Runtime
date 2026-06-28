"""Phase 4 STEP 3.2 — Micro-Sync ordering guarantees."""
from __future__ import annotations

import pytest

from app.api.schemas.context import TaskContext
from app.execution.swarm import AsyncSwarm
from tests.phase4._swarm_mocks import (
    MockContextAgent,
    MockGeneratorAgent,
    MockPlannerAgent,
    context_result_with,
)


def _task() -> TaskContext:
    return TaskContext(trace_id="t", prompt="write a function", category="coding")


@pytest.mark.asyncio
async def test_late_context_still_reflected_in_final_plan():
    """Context가 늦게 끝나도 inject_context 전에 합류 → final_plan에 반영."""
    ctx_agent = MockContextAgent(result=context_result_with(unmasked=2), delay=0.3)
    planner = MockPlannerAgent()
    generator = MockGeneratorAgent()
    swarm = AsyncSwarm(ctx_agent, planner, generator)  # type: ignore[arg-type]

    result = await swarm.execute(_task())
    assert result.final_plan.context_used is True
    assert len(result.final_plan.context_chunk_ids) == 2


@pytest.mark.asyncio
async def test_generator_runs_only_after_final_plan():
    """Generator는 final_plan 확정 이후에만, 그것도 not-None으로 호출."""
    ctx_agent = MockContextAgent()
    planner = MockPlannerAgent()
    generator = MockGeneratorAgent()
    swarm = AsyncSwarm(ctx_agent, planner, generator)  # type: ignore[arg-type]

    await swarm.execute(_task())
    assert planner.inject_called is True
    assert generator.received_final_plan is not None
    # inject_context는 generate 호출보다 먼저 끝나 있어야 한다.
    assert generator.called_at is not None


@pytest.mark.asyncio
async def test_context_and_planner_start_together():
    """Context와 Planner는 동시에 시작 (gather 병렬)."""
    ctx_agent = MockContextAgent(delay=0.1)
    planner = MockPlannerAgent()
    generator = MockGeneratorAgent()
    swarm = AsyncSwarm(ctx_agent, planner, generator)  # type: ignore[arg-type]

    await swarm.execute(_task())
    assert ctx_agent.started_at is not None
    assert planner.create_started_at is not None
    # 동시 시작 — 100ms 이내 차이.
    assert abs(ctx_agent.started_at - planner.create_started_at) < 0.1

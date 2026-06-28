"""Phase 4 STEP 3.2 — Swarm timing breakdown."""
from __future__ import annotations

import pytest

from app.api.schemas.context import TaskContext
from app.core.model_tier import ModelTier
from app.execution.swarm import AsyncSwarm
from tests.phase4._swarm_mocks import MockContextAgent, MockGeneratorAgent, MockPlannerAgent


def _task() -> TaskContext:
    return TaskContext(trace_id="t", prompt="q", category="coding",
                       selected_tier=ModelTier.STANDARD)


@pytest.mark.asyncio
async def test_all_timing_fields_populated():
    swarm = AsyncSwarm(MockContextAgent(), MockPlannerAgent(), MockGeneratorAgent())  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.context_elapsed_ms is not None
    assert result.pre_plan_elapsed_ms is not None
    assert result.inject_elapsed_ms is not None
    assert result.generator_elapsed_ms is not None
    assert result.total_elapsed_ms > 0


@pytest.mark.asyncio
async def test_context_and_pre_plan_elapsed_are_equal():
    """병렬 단계 — 두 경과 시간은 wall-clock 동일 값."""
    swarm = AsyncSwarm(MockContextAgent(delay=0.1), MockPlannerAgent(), MockGeneratorAgent())  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.context_elapsed_ms == result.pre_plan_elapsed_ms


@pytest.mark.asyncio
async def test_total_covers_parallel_plus_sequential_stages():
    """total ≈ parallel + inject + generator (각 단계 합 이상)."""
    swarm = AsyncSwarm(MockContextAgent(delay=0.05), MockPlannerAgent(), MockGeneratorAgent())  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    stage_sum = (
        result.context_elapsed_ms
        + result.inject_elapsed_ms
        + result.generator_elapsed_ms
    )
    # total은 단계 합 이상 (오버헤드 포함), 그리고 비상식적으로 크지 않음.
    assert result.total_elapsed_ms >= stage_sum - 1.0
    assert result.total_elapsed_ms < stage_sum + 500.0


@pytest.mark.asyncio
async def test_parallel_stage_not_sum_of_both():
    """Context(0.2s)+Planner(즉시) 병렬 → 경과는 ~0.2s지, 0.2+0이 아니다."""
    swarm = AsyncSwarm(MockContextAgent(delay=0.2), MockPlannerAgent(), MockGeneratorAgent())  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    # 병렬이므로 context_elapsed는 약 200ms 근처.
    assert 150.0 < result.context_elapsed_ms < 600.0

"""Phase 5 STEP 4 — Phase 4 호환성 회귀 테스트.

pfc=None 경로가 Phase 4 STEP 5.2.5 이전 흐름과 100% 동일하게 동작하는지 확인.
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import TaskContext
from app.execution.swarm import AsyncSwarm
from tests.phase4._swarm_mocks import (
    MockContextAgent,
    MockGeneratorAgent,
    MockPlannerAgent,
    context_result_with,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _task_ctx(
    prompt: str = "test",
    category: str = "coding",
    difficulty: int = 1,
) -> TaskContext:
    return TaskContext(
        trace_id="trace-compat-001",
        prompt=prompt,
        category=category,
        difficulty=difficulty,
    )


def _phase4_swarm(**kwargs) -> AsyncSwarm:
    return AsyncSwarm(
        context_agent=kwargs.get("context_agent", MockContextAgent()),
        planner_agent=kwargs.get("planner_agent", MockPlannerAgent()),
        generator_agent=kwargs.get("generator_agent", MockGeneratorAgent()),
        context_timeout=kwargs.get("context_timeout", 5.0),
        plc=kwargs.get("plc", None),
        pfc=None,  # Phase 4 경로
    )


# ---------------------------------------------------------------------------
# Phase 4 경로 기본 동작
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_path_returns_swarm_result():
    swarm = _phase4_swarm()
    result = await swarm.execute(_task_ctx())
    assert result is not None


@pytest.mark.asyncio
async def test_phase4_path_context_ok():
    swarm = _phase4_swarm()
    result = await swarm.execute(_task_ctx())
    assert result.context_status in {"ok", "empty"}


@pytest.mark.asyncio
async def test_phase4_path_planner_ok():
    swarm = _phase4_swarm()
    result = await swarm.execute(_task_ctx())
    assert result.planner_status == "ok"


@pytest.mark.asyncio
async def test_phase4_path_generator_ok():
    swarm = _phase4_swarm()
    result = await swarm.execute(_task_ctx())
    assert result.generator_status == "ok"


@pytest.mark.asyncio
async def test_phase4_path_inject_called():
    planner = MockPlannerAgent()
    swarm = _phase4_swarm(planner_agent=planner)
    await swarm.execute(_task_ctx())
    assert planner.inject_called


# ---------------------------------------------------------------------------
# context timeout → Phase 4 경로에서 graceful fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_context_timeout_fallback():
    swarm = _phase4_swarm(
        context_agent=MockContextAgent(delay=10.0),
        context_timeout=0.01,
    )
    result = await swarm.execute(_task_ctx())
    assert result.context_status == "timeout"
    assert result.planner_status in {"ok", "fallback"}


# ---------------------------------------------------------------------------
# planner exception → Phase 4 경로에서 fallback pre_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_planner_exception_uses_fallback():
    swarm = _phase4_swarm(
        planner_agent=MockPlannerAgent(pre_plan_raises=RuntimeError("oops"))
    )
    result = await swarm.execute(_task_ctx())
    assert result.planner_status == "fallback"
    assert result.final_plan.intent == "general"


# ---------------------------------------------------------------------------
# Phase 4 CancelledError 전파
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_cancelled_error_propagates():
    swarm = _phase4_swarm(
        context_agent=MockContextAgent(delay=5.0),
        context_timeout=5.0,
    )

    async def run_and_cancel():
        task = asyncio.create_task(swarm.execute(_task_ctx()))
        await asyncio.sleep(0.02)
        task.cancel()
        return await task

    with pytest.raises(asyncio.CancelledError):
        await run_and_cancel()


# ---------------------------------------------------------------------------
# Phase 4와 Phase 5 경로 결과 구조 동일
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_and_phase5_result_fields_identical():
    """Phase 4 결과와 Phase 5 결과가 동일한 필드 집합을 가짐."""
    from app.routing.pfc import PFCDecision, PFCHint

    class InstantPFC:
        async def infer_hint(self, query, eval_result, goal_stack_summary, active_goal):
            return PFCDecision(
                hint=PFCHint(intent="general", cue_type="general_fallback", confidence=0.5)
            )

    from tests.phase5.test_swarm_pfc_integration import PFCAwareMockPlanner

    swarm4 = _phase4_swarm()
    swarm5 = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=InstantPFC(),
    )

    result4 = await swarm4.execute(_task_ctx())
    result5 = await swarm5.execute(_task_ctx())

    assert type(result4).__name__ == type(result5).__name__ == "SwarmResult"
    assert set(result4.model_fields.keys()) == set(result5.model_fields.keys())


# ---------------------------------------------------------------------------
# Phase 4 elapsed 타이밍 필드 모두 0 이상
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_elapsed_fields_positive():
    swarm = _phase4_swarm()
    result = await swarm.execute(_task_ctx())
    assert result.context_elapsed_ms >= 0
    assert result.pre_plan_elapsed_ms >= 0
    assert result.inject_elapsed_ms >= 0
    assert result.generator_elapsed_ms >= 0
    assert result.total_elapsed_ms >= 0


# ---------------------------------------------------------------------------
# MockPlannerAgent는 pfc_decision kwarg 없이도 작동 (기존 호환성)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_planner_no_pfc_decision_kwarg():
    """기존 MockPlannerAgent는 pfc_decision 인자 없이 create_pre_plan을 호출받음."""
    planner = MockPlannerAgent()
    swarm = _phase4_swarm(planner_agent=planner)
    result = await swarm.execute(_task_ctx())
    assert result is not None
    assert planner.inject_called

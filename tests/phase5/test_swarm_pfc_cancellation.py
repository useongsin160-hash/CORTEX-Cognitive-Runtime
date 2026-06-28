"""Phase 5 STEP 4 — AsyncSwarm PFC CancelledError 처리 테스트.

핵심 불변 조건: CancelledError는 절대 삼키지 않는다.
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import TaskContext
from app.execution.swarm import AsyncSwarm
from app.routing.pfc import PFCDecision, PFCHint, PFCIntegrationConfig
from tests.phase4._swarm_mocks import (
    MockContextAgent,
    MockGeneratorAgent,
)
from tests.phase5.test_swarm_pfc_integration import (
    PFCAwareMockPlanner,
    MockPFCSlow,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _task_ctx(prompt: str = "cancel test") -> TaskContext:
    return TaskContext(
        trace_id="trace-cancel-001",
        prompt=prompt,
        category="coding",
        difficulty=1,
    )


def _decision() -> PFCDecision:
    return PFCDecision(
        hint=PFCHint(intent="general", cue_type="general_fallback", confidence=0.5)
    )


# ---------------------------------------------------------------------------
# outer CancelledError 전파 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outer_cancel_propagates_during_pfc_wait():
    """outer task가 cancel되면 CancelledError가 re-raise됨."""
    slow_pfc = MockPFCSlow(_decision(), delay=5.0)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=30.0, max_hint_timeout_ms=5000.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(delay=5.0),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=slow_pfc,
        pfc_config=pfc_config,
    )

    async def run_and_cancel():
        task = asyncio.create_task(swarm.execute(_task_ctx()))
        await asyncio.sleep(0.05)
        task.cancel()
        return await task

    with pytest.raises(asyncio.CancelledError):
        await run_and_cancel()


@pytest.mark.asyncio
async def test_outer_cancel_propagates_during_context_join():
    """context join 중 outer cancel → CancelledError re-raise."""
    slow_pfc = MockPFCSlow(_decision(), delay=0.0)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=30.0, max_hint_timeout_ms=5000.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(delay=5.0),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=slow_pfc,
        pfc_config=pfc_config,
    )

    async def run_and_cancel():
        task = asyncio.create_task(swarm.execute(_task_ctx()))
        await asyncio.sleep(0.05)
        task.cancel()
        return await task

    with pytest.raises(asyncio.CancelledError):
        await run_and_cancel()


# ---------------------------------------------------------------------------
# TimeoutError는 CancelledError가 아님 — 진행 계속
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_timeout_is_not_cancelled_error():
    """PFC timeout 시 TimeoutError를 CancelledError로 잘못 처리하지 않음."""
    slow_pfc = MockPFCSlow(_decision(), delay=1.0)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=10.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=slow_pfc,
        pfc_config=pfc_config,
    )
    # CancelledError가 아니라 정상적으로 완료되어야 한다
    result = await swarm.execute(_task_ctx())
    assert result is not None


# ---------------------------------------------------------------------------
# asyncio.shield 보장: pfc_task는 outer timeout 후에도 계속 실행
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_task_continues_after_bounded_timeout():
    """asyncio.shield 덕에 pfc_task는 timeout 후에도 완료될 수 있어야 한다."""
    completed = []

    class TrackingPFC:
        async def infer_hint(self, query, eval_result, goal_stack_summary, active_goal):
            await asyncio.sleep(0.05)
            completed.append(True)
            return PFCDecision(
                hint=PFCHint(
                    intent="general",
                    cue_type="general_fallback",
                    confidence=0.5,
                )
            )

    pfc_config = PFCIntegrationConfig(hint_timeout_ms=5.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=TrackingPFC(),
        pfc_config=pfc_config,
    )
    result = await swarm.execute(_task_ctx())
    # Give background task time to complete
    await asyncio.sleep(0.1)
    assert result is not None
    assert completed, "PFC task should continue running after bounded timeout"


# ---------------------------------------------------------------------------
# _cleanup_tasks: done task는 cancel하지 않음
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_done_task_is_safe():
    """_cleanup_tasks가 이미 완료된 task에 대해 안전하게 동작."""
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=None,
    )
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    # 완료된 task를 cleanup해도 예외 없음
    await swarm._cleanup_tasks(pfc_task=done_task, context_task=None)

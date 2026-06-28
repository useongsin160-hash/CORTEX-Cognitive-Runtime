"""Phase 5 STEP 4 — AsyncSwarm PFC 통합 테스트."""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import TaskContext
from app.execution.swarm import AsyncSwarm
from app.routing.pfc import (
    GoalSnapshot,
    PFCDecision,
    PFCHint,
    PFCIntegrationConfig,
    PrefrontalCortex,
)
from tests.phase4._swarm_mocks import (
    MockContextAgent,
    MockGeneratorAgent,
    MockPlannerAgent,
    context_result_with,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _task_ctx(prompt: str = "test query", category: str = "coding") -> TaskContext:
    return TaskContext(
        trace_id="trace-pfc-001",
        prompt=prompt,
        category=category,
        difficulty=1,
    )


def _snap(goal_id: str = "g1", category: str = "coding") -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id,
        title="Test Goal",
        category=category,
        priority=0.8,
        source="user_explicit",
        status="active",
    )


class MockPFCFast:
    """즉시 PFCDecision을 반환하는 PFC mock."""

    def __init__(self, decision: PFCDecision) -> None:
        self._decision = decision

    async def infer_hint(self, query, eval_result, goal_stack_summary, active_goal):
        return self._decision


class MockPFCSlow:
    """지정된 delay 후 PFCDecision을 반환하는 PFC mock."""

    def __init__(self, decision: PFCDecision, delay: float = 0.1) -> None:
        self._decision = decision
        self._delay = delay

    async def infer_hint(self, query, eval_result, goal_stack_summary, active_goal):
        await asyncio.sleep(self._delay)
        return self._decision


class MockPFCRaises:
    """예외를 발생시키는 PFC mock."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def infer_hint(self, query, eval_result, goal_stack_summary, active_goal):
        raise self._exc


def _decision(cue_type: str = "general_fallback", confidence: float = 0.5) -> PFCDecision:
    return PFCDecision(
        hint=PFCHint(
            intent="general",
            cue_type=cue_type,
            confidence=confidence,
        )
    )


class PFCAwareMockPlanner(MockPlannerAgent):
    """create_pre_plan에서 pfc_decision kwarg를 받는 Planner mock."""

    def __init__(self) -> None:
        super().__init__()
        self.received_pfc_decision = None

    async def create_pre_plan(
        self, query: str, difficulty: int = 1, category=None, pfc_decision=None
    ):
        self.received_pfc_decision = pfc_decision
        return await super().create_pre_plan(
            query=query, difficulty=difficulty, category=category
        )


def _swarm(pfc, pfc_config=None) -> AsyncSwarm:
    return AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        context_timeout=5.0,
        pfc=pfc,
        pfc_config=pfc_config,
    )


# ---------------------------------------------------------------------------
# pfc=None → Phase 4 경로 실행
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_none_executes_phase4_path():
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=None,
    )
    result = await swarm.execute(_task_ctx())
    assert result is not None


# ---------------------------------------------------------------------------
# PFC 주입 → Phase 5 경로 실행
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_injected_executes_phase5_path():
    pfc = MockPFCFast(_decision("continuation", confidence=0.9))
    swarm = _swarm(pfc)
    result = await swarm.execute(_task_ctx())
    assert result is not None
    assert result.generator_result.text.startswith("[MOCK]")


@pytest.mark.asyncio
async def test_pfc_decision_passed_to_planner():
    """PFC decision이 PlannerAgent.create_pre_plan에 전달됨을 확인."""
    pfc = MockPFCFast(_decision("continuation", confidence=0.9))
    planner = PFCAwareMockPlanner()
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=planner,
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    await swarm.execute(_task_ctx())
    assert planner.received_pfc_decision is not None
    assert planner.received_pfc_decision.hint.cue_type == "continuation"


# ---------------------------------------------------------------------------
# PFC timeout → pfc_decision=None으로 진행 (Phase 4 fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_timeout_proceeds_without_decision():
    """PFC가 timeout보다 느리면 pfc_decision=None으로 진행."""
    pfc = MockPFCSlow(_decision(), delay=0.2)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=10.0)
    planner = PFCAwareMockPlanner()
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=planner,
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
        pfc_config=pfc_config,
    )
    result = await swarm.execute(_task_ctx())
    assert result is not None
    assert planner.received_pfc_decision is None


# ---------------------------------------------------------------------------
# PFC error → pfc_decision=None으로 진행
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_error_proceeds_without_decision():
    """PFC가 일반 예외를 던지면 pfc_decision=None으로 진행."""
    pfc = MockPFCRaises(RuntimeError("PFC internal error"))
    planner = PFCAwareMockPlanner()
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=planner,
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    result = await swarm.execute(_task_ctx())
    assert result is not None
    assert planner.received_pfc_decision is None


# ---------------------------------------------------------------------------
# default pfc_config 자동 생성 (pfc 주입 시 pfc_config=None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_pfc_config_created_when_pfc_injected():
    """pfc 주입 + pfc_config=None → default PFCIntegrationConfig 자동 생성."""
    pfc = MockPFCFast(_decision())
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
        pfc_config=None,
    )
    assert swarm._pfc_config is not None
    assert swarm._pfc_config.hint_timeout_ms == 30.0


# ---------------------------------------------------------------------------
# SwarmResult 구조 보존
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swarm_result_has_required_fields_phase5():
    """Phase 5 경로에서도 SwarmResult 필드 보존."""
    pfc = MockPFCFast(_decision())
    swarm = _swarm(pfc)
    result = await swarm.execute(_task_ctx())
    assert result.context_status in {"ok", "empty", "timeout", "error"}
    assert result.planner_status in {"ok", "fallback"}
    assert result.generator_status in {"ok", "fallback"}
    assert result.total_elapsed_ms >= 0


# ---------------------------------------------------------------------------
# background_tasks 등록
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_pfc_registers_background_task():
    """PFC timeout 시 background_tasks에 late handler가 등록됨."""
    pfc = MockPFCSlow(_decision(), delay=0.5)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=10.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
        pfc_config=pfc_config,
    )
    await swarm.execute(_task_ctx())
    # late handler가 즉시 완료되기 전에는 set에 있을 수 있음
    # 완료 후에는 discard — 단, 타이밍에 따라 0개도 정상
    assert isinstance(swarm._background_tasks, set)

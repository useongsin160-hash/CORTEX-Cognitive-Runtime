"""Phase 4 STEP 3.2 — Swarm exception isolation."""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import TaskContext
from app.core.model_tier import ModelTier
from app.execution.generator_agent import GeneratorAgent
from app.execution.mock_llm_client import MockLLMClient
from app.execution.swarm import AsyncSwarm
from app.routing.neuromodulators import Norepinephrine
from tests.phase4._swarm_mocks import (
    MockContextAgent,
    MockGeneratorAgent,
    MockPlannerAgent,
)


def _task() -> TaskContext:
    return TaskContext(trace_id="t", prompt="write a function",
                       category="coding", selected_tier=ModelTier.STANDARD)


@pytest.mark.asyncio
async def test_context_error_falls_back_to_empty():
    ctx = MockContextAgent(raises=ValueError("synthetic context failure"))
    swarm = AsyncSwarm(ctx, MockPlannerAgent(), MockGeneratorAgent())  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.context_status == "error"
    assert result.final_plan.context_status == "error"
    assert result.context_result is None
    # Generator still produced output despite the context failure.
    assert result.generator_result.text


@pytest.mark.asyncio
async def test_context_timeout_marks_timeout_status():
    # Context sleeps 2s but the swarm timeout is 0.2s.
    ctx = MockContextAgent(delay=2.0)
    swarm = AsyncSwarm(
        ctx, MockPlannerAgent(), MockGeneratorAgent(),  # type: ignore[arg-type]
        context_timeout=0.2,
    )
    result = await swarm.execute(_task())
    assert result.context_status == "timeout"
    assert result.context_result is None


@pytest.mark.asyncio
async def test_planner_error_falls_back_to_general_pre_plan():
    planner = MockPlannerAgent(pre_plan_raises=RuntimeError("synthetic planner failure"))
    swarm = AsyncSwarm(MockContextAgent(), planner, MockGeneratorAgent())  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.planner_status == "fallback"
    # fallback pre_plan → final_plan intent "general"
    assert result.final_plan.intent == "general"


@pytest.mark.asyncio
async def test_generator_failure_yields_fallback_candidate():
    class _FailingLLM:
        async def generate(self, prompt, tier, params, vendor="anthropic"):
            raise RuntimeError("synthetic llm outage")

    real_generator = GeneratorAgent(_FailingLLM(), Norepinephrine())  # type: ignore[arg-type]
    swarm = AsyncSwarm(MockContextAgent(), MockPlannerAgent(), real_generator)  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.generator_status == "fallback"
    assert result.generator_result.fallback_candidate is not None
    assert "synthetic llm outage" in result.generator_result.fallback_candidate


@pytest.mark.asyncio
async def test_context_and_planner_both_fail_generator_still_runs():
    ctx = MockContextAgent(raises=ValueError("ctx down"))
    planner = MockPlannerAgent(pre_plan_raises=RuntimeError("planner down"))
    real_generator = GeneratorAgent(MockLLMClient(), Norepinephrine())
    swarm = AsyncSwarm(ctx, planner, real_generator)  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.context_status == "error"
    assert result.planner_status == "fallback"
    # Generator still produced a normal (non-fallback) result.
    assert result.generator_status == "ok"
    assert result.generator_result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_cancelled_error_is_not_swallowed():
    """Context가 CancelledError를 던지면 swarm은 그대로 propagate."""
    ctx = MockContextAgent(raises=asyncio.CancelledError())
    swarm = AsyncSwarm(ctx, MockPlannerAgent(), MockGeneratorAgent())  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await swarm.execute(_task())


@pytest.mark.asyncio
async def test_planner_cancelled_error_is_not_swallowed():
    planner = MockPlannerAgent(pre_plan_raises=asyncio.CancelledError())
    swarm = AsyncSwarm(MockContextAgent(), planner, MockGeneratorAgent())  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await swarm.execute(_task())

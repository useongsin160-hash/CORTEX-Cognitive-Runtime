"""Phase 4 STEP 3.2 — Generator는 final_plan으로만 호출된다."""
from __future__ import annotations

import pytest

from app.api.schemas.context import TaskContext
from app.core.model_tier import ModelTier
from app.execution.generator_agent import GeneratorAgent
from app.execution.mock_llm_client import MockLLMClient
from app.execution.plan_models import FinalPlan
from app.execution.swarm import AsyncSwarm
from app.routing.neuromodulators import Norepinephrine
from tests.phase4._swarm_mocks import MockContextAgent, MockGeneratorAgent, MockPlannerAgent


def _task() -> TaskContext:
    return TaskContext(trace_id="t", prompt="q", category="coding",
                       selected_tier=ModelTier.STANDARD)


@pytest.mark.asyncio
async def test_generator_receives_a_final_plan_instance():
    generator = MockGeneratorAgent()
    swarm = AsyncSwarm(MockContextAgent(), MockPlannerAgent(), generator)  # type: ignore[arg-type]
    await swarm.execute(_task())
    assert isinstance(generator.received_final_plan, FinalPlan)


@pytest.mark.asyncio
async def test_generator_rejects_none_final_plan_directly():
    """STEP 3.1 회귀 — pre_plan-only 호출은 RuntimeError."""
    agent = GeneratorAgent(MockLLMClient(), Norepinephrine())
    with pytest.raises(RuntimeError, match="requires final_plan"):
        await agent.generate(None, _task())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_swarm_flow_passes_generator_final_plan_validation():
    """Swarm 흐름 전체에서 Generator가 정상 결과를 낸다 (final_plan 통과)."""
    real_generator = GeneratorAgent(MockLLMClient(), Norepinephrine())
    swarm = AsyncSwarm(MockContextAgent(), MockPlannerAgent(), real_generator)  # type: ignore[arg-type]
    result = await swarm.execute(_task())
    assert result.generator_result.finish_reason == "stop"

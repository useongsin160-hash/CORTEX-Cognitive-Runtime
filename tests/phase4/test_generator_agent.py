"""Phase 4 STEP 3.1 — GeneratorAgent (final_plan + Norepinephrine)."""
from __future__ import annotations

import json

import pytest

from app.api.schemas.context import TaskContext
from app.core.model_tier import ModelTier
from app.execution.generator_agent import GeneratorAgent
from app.execution.mock_llm_client import MockLLMClient
from app.execution.params import GenerationParams
from app.execution.plan_models import FinalPlan
from app.routing.neuromodulators import Norepinephrine


def _final_plan() -> FinalPlan:
    return FinalPlan(intent="answer", steps=["s1"], prompt_for_generator="[QUERY] q")


def _ctx(tier: ModelTier, ne_boost: bool) -> TaskContext:
    return TaskContext(trace_id="t", selected_tier=tier, ne_boost=ne_boost)


def _agent() -> GeneratorAgent:
    return GeneratorAgent(MockLLMClient(), Norepinephrine())


class _FailingLLMClient:
    async def generate(self, prompt, tier, params, vendor="anthropic"):
        raise RuntimeError("synthetic llm outage")


@pytest.mark.asyncio
async def test_none_final_plan_raises_runtime_error():
    agent = _agent()
    with pytest.raises(RuntimeError, match="requires final_plan"):
        await agent.generate(None, _ctx(ModelTier.STANDARD, False))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_normal_generation_returns_result():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.STANDARD, False))
    assert result.text
    assert result.finish_reason == "stop"
    assert result.plan_intent == "answer"


@pytest.mark.asyncio
async def test_tier_used_is_name_string():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.DEEP_THINKING, False))
    assert result.tier_used == "DEEP_THINKING"
    assert isinstance(result.tier_used, str)


@pytest.mark.asyncio
async def test_ne_applied_when_boost_and_high_tier():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.DEEP_THINKING, True))
    assert result.ne_applied is True
    assert result.ne_reason == "high_difficulty"


@pytest.mark.asyncio
async def test_ne_mismatch_when_boost_but_low_tier():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.MEDIUM, True))
    assert result.ne_applied is False
    assert result.ne_reason == "tier_mismatch"


@pytest.mark.asyncio
async def test_no_ne_when_boost_false():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.HEAVY, False))
    assert result.ne_applied is False
    assert result.ne_reason is None


@pytest.mark.asyncio
async def test_default_params_used_when_none():
    agent = _agent()
    # base_params omitted → default GenerationParams; no NE → temp stays 0.7.
    result = await agent.generate(_final_plan(), _ctx(ModelTier.STANDARD, False))
    assert "temp=0.7" in result.text
    assert "top_k=40" in result.text


@pytest.mark.asyncio
async def test_ne_modulates_default_params():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.DEEP_THINKING, True))
    # default 0.7 → min(0.7, 0.1) = 0.1; default 40 → max(40, 80) = 80
    assert "temp=0.1" in result.text
    assert "top_k=80" in result.text


@pytest.mark.asyncio
async def test_llm_failure_returns_fallback():
    agent = GeneratorAgent(_FailingLLMClient(), Norepinephrine())  # type: ignore[arg-type]
    result = await agent.generate(_final_plan(), _ctx(ModelTier.STANDARD, False))
    assert result.text.startswith("[FALLBACK]")
    assert result.finish_reason == "error"
    assert result.fallback_candidate is not None
    assert "synthetic llm outage" in result.fallback_candidate


@pytest.mark.asyncio
async def test_generator_result_json_serializable():
    agent = _agent()
    result = await agent.generate(_final_plan(), _ctx(ModelTier.STANDARD, False))
    dumped = json.loads(result.model_dump_json())
    assert "text" in dumped
    assert "plan_intent" in dumped

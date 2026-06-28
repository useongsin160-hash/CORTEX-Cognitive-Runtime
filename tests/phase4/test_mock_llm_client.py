"""Phase 4 STEP 1 — MockLLMClient."""
from __future__ import annotations

import pytest

from app.core.model_tier import ModelTier, resolve_model
from app.execution.mock_llm_client import MockLLMClient
from app.execution.params import GenerationParams


@pytest.mark.asyncio
async def test_generate_returns_llm_result():
    client = MockLLMClient()
    result = await client.generate("hello world", ModelTier.STANDARD, GenerationParams())
    assert result.text
    assert result.finish_reason == "stop"
    assert result.mode == "mock"


@pytest.mark.asyncio
async def test_tier_used_is_name_string():
    client = MockLLMClient()
    result = await client.generate("q", ModelTier.DEEP_THINKING, GenerationParams())
    assert result.tier_used == "DEEP_THINKING"
    assert isinstance(result.tier_used, str)


@pytest.mark.asyncio
async def test_model_name_resolved_from_registry():
    client = MockLLMClient()
    result = await client.generate("q", ModelTier.HEAVY, GenerationParams(), vendor="anthropic")
    assert result.model_name == resolve_model("anthropic", ModelTier.HEAVY)


@pytest.mark.asyncio
async def test_latency_increases_with_tier():
    client = MockLLMClient()
    light = await client.generate("q", ModelTier.LIGHTWEIGHT, GenerationParams())
    deep = await client.generate("q", ModelTier.DEEP_THINKING, GenerationParams())
    assert deep.latency_ms > light.latency_ms


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google", "openai"])
async def test_all_vendors_resolve(vendor):
    client = MockLLMClient()
    result = await client.generate("q", ModelTier.STANDARD, GenerationParams(), vendor=vendor)
    assert result.model_name == resolve_model(vendor, ModelTier.STANDARD)


@pytest.mark.asyncio
async def test_ne_applied_params_produce_ne_marker():
    client = MockLLMClient()
    params = GenerationParams(ne_applied=True, ne_reason="high_difficulty")
    result = await client.generate("q", ModelTier.HEAVY, params)
    assert "[NE]" in result.text


@pytest.mark.asyncio
async def test_no_ne_marker_when_ne_not_applied():
    client = MockLLMClient()
    result = await client.generate("q", ModelTier.HEAVY, GenerationParams())
    assert "[NE]" not in result.text


@pytest.mark.asyncio
async def test_token_counts_are_positive():
    client = MockLLMClient()
    result = await client.generate("a longer prompt for token estimation", ModelTier.STANDARD, GenerationParams())
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0


@pytest.mark.asyncio
async def test_params_used_round_trips():
    client = MockLLMClient()
    params = GenerationParams(temperature=0.2, top_k=99)
    result = await client.generate("q", ModelTier.STANDARD, params)
    assert result.params_used.temperature == 0.2
    assert result.params_used.top_k == 99

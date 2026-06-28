"""B1 — Tier15Augmentation.execute() diff-edit behavior (injected fake client).

Covers: execute returns the client's text, calls the LIGHTWEIGHT (Flash) tier
with a diff-edit prompt containing both the new prompt and the cached answer,
falls back to the cached answer on any LLM failure (exception or
finish_reason="error") without surfacing provider text, and re-raises
CancelledError. No mock branch lives in the class — the client is injected.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.model_tier import ModelTier
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams
from app.routing.tier1_5 import Tier15Augmentation


def _result(text: str, *, finish_reason: str = "stop") -> LLMResult:
    return LLMResult(
        text=text,
        tier_used="LIGHTWEIGHT",
        model_name="m",
        prompt_tokens=1,
        completion_tokens=1,
        finish_reason=finish_reason,
        mode="mock",
        latency_ms=1.0,
        params_used=GenerationParams(),
    )


class _RecordingClient:
    def __init__(self, *, result=None, raise_exc=None):
        self.calls: list[dict] = []
        self._result = result
        self._raise = raise_exc

    async def generate(self, prompt, tier, params, vendor="anthropic"):
        self.calls.append({"prompt": prompt, "tier": tier, "params": params})
        if self._raise is not None:
            raise self._raise
        return self._result


@pytest.mark.asyncio
async def test_execute_returns_client_text():
    client = _RecordingClient(result=_result("edited answer"))
    out = await Tier15Augmentation(llm_client=client).execute("new q", "cached a")
    assert out == "edited answer"


@pytest.mark.asyncio
async def test_execute_calls_lightweight_tier_with_diff_edit_prompt():
    client = _RecordingClient(result=_result("ok"))
    await Tier15Augmentation(llm_client=client).execute("NEWQ", "CACHEDANS")
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["tier"] == ModelTier.LIGHTWEIGHT
    # the diff-edit prompt carries both the new question and the cached answer.
    assert "NEWQ" in call["prompt"]
    assert "CACHEDANS" in call["prompt"]
    assert isinstance(call["params"], GenerationParams)


@pytest.mark.asyncio
async def test_execute_falls_back_to_cache_on_exception():
    client = _RecordingClient(raise_exc=RuntimeError("provider boom"))
    out = await Tier15Augmentation(llm_client=client).execute("q", "the cached answer")
    assert out == "the cached answer"  # graceful — no provider text leaks


@pytest.mark.asyncio
async def test_execute_falls_back_to_cache_on_error_finish():
    client = _RecordingClient(result=_result("junk", finish_reason="error"))
    out = await Tier15Augmentation(llm_client=client).execute("q", "cached fallback")
    assert out == "cached fallback"


@pytest.mark.asyncio
async def test_execute_reraises_cancelled():
    client = _RecordingClient(raise_exc=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await Tier15Augmentation(llm_client=client).execute("q", "c")

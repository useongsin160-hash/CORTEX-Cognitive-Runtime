"""Mock LLM 클라이언트 — 실제 API 호출 없는 결정론적 응답."""
from __future__ import annotations

import asyncio
import time

from app.core.model_tier import ModelTier, resolve_model
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams


class MockLLMClient:
    """실제 API 호출 없이 결정론적 응답을 반환하는 클라이언트.

    회귀 테스트와 개발 환경에서 사용. Latency는 tier별로 시뮬레이션.

    응답 형식:
      "[MOCK {tier_name}{ [NE] }] {prompt 앞 50자}... (temp=, top_k=)"
    """

    _LATENCY_MAP = {
        ModelTier.LIGHTWEIGHT: 10,
        ModelTier.MEDIUM: 30,
        ModelTier.STANDARD: 80,
        ModelTier.HEAVY: 150,
        ModelTier.DEEP_THINKING: 300,
    }

    async def generate(
        self,
        prompt: str,
        tier: ModelTier,
        params: GenerationParams,
        vendor: str = "anthropic",
    ) -> LLMResult:
        start = time.perf_counter()

        await asyncio.sleep(self._LATENCY_MAP[tier] / 1000)

        model_name = resolve_model(vendor, tier)

        prompt_preview = prompt[:50] + ("..." if len(prompt) > 50 else "")
        ne_suffix = " [NE]" if params.ne_applied else ""
        text = (
            f"[MOCK {tier.name}{ne_suffix}] {prompt_preview} "
            f"(temp={params.temperature}, top_k={params.top_k})"
        )

        # Token count 시뮬레이션 (단순 추정 — 실제 토크나이저 아님).
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(text) // 4

        latency_ms = (time.perf_counter() - start) * 1000

        return LLMResult(
            text=text,
            tier_used=tier.name,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason="stop",
            mode="mock",
            latency_ms=latency_ms,
            params_used=params,
        )

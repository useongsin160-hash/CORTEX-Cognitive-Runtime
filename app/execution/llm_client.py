"""LLM 호출 인터페이스 — Protocol + 결과 모델."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.core.model_tier import ModelTier
from app.execution.params import GenerationParams


class LLMResult(BaseModel):
    """LLM 호출 결과."""

    text: str
    tier_used: str          # ModelTier.name (직렬화 안전 — 정수 노출 금지)
    model_name: str         # resolve_model() 결과
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str      # "stop" / "length" / "error"

    # 디버깅용
    mode: str               # "mock" / "live"
    latency_ms: float
    params_used: GenerationParams


class LLMClientProtocol(Protocol):
    """LLM 호출 인터페이스.

    구현체:
      - MockLLMClient: 기본 (회귀 테스트, 개발)
      - LiveLLMClient: 실제 API 호출 (수동 활성화)

    Phase 4 STEP 1: MockLLMClient만 구현.
    Phase 4 STEP 5 이후: LiveLLMClient 활성화 결정.
    """

    async def generate(
        self,
        prompt: str,
        tier: ModelTier,
        params: GenerationParams,
        vendor: str = "anthropic",
    ) -> LLMResult:
        ...

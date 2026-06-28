"""Planner / Generator 데이터 모델 — 순수 Pydantic."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# "skipped": B11 S3b-demote — lightweight route skips Context Agent retrieval
# (not an error/timeout; pipeline treats it as non-error).
ContextStatus = Literal["ok", "empty", "error", "timeout", "skipped"]


class PrePlan(BaseModel):
    """Context 없이 생성된 임시 뼈대 (Micro-Sync 1단계, 설계서 line 274).

    Planner가 쿼리의 구조적 의도만 파악해 임시 뼈대를 수립한다.
    이 단계에서는 ContextAgentResult를 참조하지 않는다.
    """

    intent: str  # answer / code_generation / analysis / creative / general
    steps_outline: list[str] = Field(default_factory=list)
    requires_context: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class FinalPlan(BaseModel):
    """Context 주입 후 확정된 실행 계획 (Micro-Sync 2단계, 설계서 line 275)."""

    intent: str
    steps: list[str] = Field(default_factory=list)
    context_used: bool = False
    context_chunk_ids: list[str] = Field(default_factory=list)
    prompt_for_generator: str

    # 디버깅용 — pre_plan 대비 steps가 변경됐는지
    pre_plan_modified: bool = False

    # STEP 3.2 Swarm 통합 시 채워짐. STEP 3.1에서는 기본값 "ok".
    # Generator가 fallback 판단 시 참조 가능.
    #   "ok"      : Context 정상 수신, 사용함
    #   "empty"   : Context 비어있음 (retrieval 결과 0건)
    #   "error"   : Context retrieval 중 예외 발생
    #   "timeout" : Context retrieval timeout 초과
    context_status: ContextStatus = "ok"
    context_fallback_reason: str | None = None


class GeneratorResult(BaseModel):
    """Generator Agent 실행 결과. MockLLMClient의 LLMResult를 wrapping."""

    text: str
    tier_used: str  # ModelTier.name
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    latency_ms: float

    # Norepinephrine 적용 여부 (디버깅)
    ne_applied: bool
    ne_reason: str | None = None

    plan_intent: str

    # 실패 시 fallback 후보 (Graceful Fallback 자리 — CP3 검증은 후속 STEP)
    fallback_candidate: str | None = None

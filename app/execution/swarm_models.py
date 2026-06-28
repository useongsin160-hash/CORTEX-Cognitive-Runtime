"""Async Swarm 실행 결과 모델 — 순수 Pydantic."""
from __future__ import annotations

from pydantic import BaseModel

from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult


class SwarmResult(BaseModel):
    """Swarm 실행 결과.

    순수 Pydantic. coroutine / task / lock / DB client 포함 금지.
    context_result는 Context 실패(error/timeout) 시 None.
    """

    context_result: ContextAgentResult | None
    final_plan: FinalPlan
    generator_result: GeneratorResult

    # 메타데이터
    context_status: str    # FinalPlan.context_status 미러링
    planner_status: str    # "ok" / "fallback"
    generator_status: str  # "ok" / "fallback"

    # 실행 시간 (디버깅용)
    context_elapsed_ms: float | None = None
    pre_plan_elapsed_ms: float | None = None
    inject_elapsed_ms: float | None = None
    generator_elapsed_ms: float | None = None
    total_elapsed_ms: float

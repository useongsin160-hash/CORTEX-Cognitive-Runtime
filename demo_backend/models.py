"""demo_backend Pydantic 모델 — 요청/응답 + CORTEX /query 결과 정규화.

CORTEX 의 내부 스키마(app.api.schemas)를 import 하지 않는다 — HTTP 응답(dict)을
demo 가 자체 모델로 다시 정규화한다(느슨한 결합).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 요청/응답 ───────────────────────────────────────────────────────────────
class DemoChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=8000)  # 본문 크기 2차 방어
    mode: str = Field(default="agent_planner")


class DemoChatResponse(BaseModel):
    run_id: str
    status: Literal["done"]
    result_url: str


# ── CORTEX /query 정규화 뷰 (설계 5 매핑) ───────────────────────────────────
class RouteDecisionView(BaseModel):
    path: str | None = None
    skip_layers: list[str] = Field(default_factory=list)
    reason: str | None = None


class SwarmTraceView(BaseModel):
    executed: bool | None = None
    status: str | None = None
    elapsed_ms: float | None = None
    context_status: str | None = None
    planner_status: str | None = None
    generator_status: str | None = None
    generator_finish_reason: str | None = None
    generator_model_name: str | None = None
    plan_intent: str | None = None


class GlycineView(BaseModel):
    active: bool = False
    reason: str | None = None
    action: str | None = None


class AnswerView(BaseModel):
    text: str = ""
    mode: Literal["stub", "live"] = "stub"
    gated: bool = True
    # 답변의 성질 라벨(정직성). early-exit를 일괄 "stub"로 뭉치지 않는다:
    #   live_generator | mock_hidden | unavailable | reflex | cache |
    #   tier_1_5_stub | safety_blocked
    # additive·optional — 기존 mode/gated/text는 비파괴 보존.
    source: str | None = None


class CortexQueryView(BaseModel):
    trace_id: str | None = None
    path_taken: str | None = None
    category: str | None = None
    difficulty: int | None = None
    route_decision: RouteDecisionView | None = None
    selected_tier: str | None = None
    epinephrine_active: bool = False
    epinephrine_reason: str | None = None
    response_source: str | None = None
    swarm_trace: SwarmTraceView | None = None
    glycine: GlycineView = Field(default_factory=GlycineView)
    answer: AnswerView = Field(default_factory=AnswerView)


class SafetyInvariants(BaseModel):
    active_learning_enabled: bool = False
    basal_ganglia_applied: bool = False
    conflict_resolution: str = "deferred"
    mutation_count: int = 0
    llm_live_enabled: bool = False


class TraceEnrichment(BaseModel):
    available: bool = False
    event_count: int = 0
    events: list[dict[str, Any]] | None = None


class NormalizedRunResult(BaseModel):
    run_id: str
    session_id: str
    status: Literal["done"] = "done"
    created_at: str  # ISO8601 UTC
    cortex: CortexQueryView
    safety_invariants: SafetyInvariants = Field(default_factory=SafetyInvariants)
    trace: TraceEnrichment = Field(default_factory=TraceEnrichment)


# ── readiness — core /health 상태를 중계 (자체 키/벤더 판단 없음) ───────────
class ReadinessResponse(BaseModel):
    cortex_reachable: bool
    cortex_url: str
    demo_mode: str
    # core /health 의 slots_ready 를 그대로 중계 (벤더 중립). 5칸 Tier Slot
    # Registry 가 전부 필요한 키를 갖췄는지의 단일 집계 불리언 — 키 값/env 이름/
    # 벤더명은 포함하지 않는다. core 미도달 시 False(graceful not-ready).
    slots_ready: bool
    llm_live_enabled: bool
    can_run_query: bool
    can_run_live_llm: bool
    active_learning_enabled: bool
    basal_ganglia_applied: bool
    conflict_resolution: str
    warnings: list[str]

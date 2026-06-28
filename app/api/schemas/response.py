from typing import Literal

from pydantic import BaseModel, Field

from app.routing.skip_router import RouteDecision

ResponseSource = Literal[
    "thalamus",
    "exact_cache",
    "semantic_cache",
    "tier_1_5",
    "swarm",
    "fallback",
]

SwarmStatus = Literal["ok", "degraded", "error", "timeout"]


class SwarmTrace(BaseModel):
    """Swarm 실행 추적 정보.

    routed/swarm 경로에서만 채워진다. early-exit 경로 (thalamus / cache /
    tier_1_5)에서는 QueryResponse.swarm_trace=None.

    PHASE 4 STEP 3.3b: 실제 채우기 시작.
    PHASE 4 STEP 3.3a (현재): 모델만 정의 — routed 경로에서도 None.
    """

    executed: bool = Field(description="Swarm이 실제 실행됐는지 여부")
    status: SwarmStatus = Field(description="Swarm 전체 상태 요약")
    elapsed_ms: float | None = Field(
        default=None, description="Swarm 전체 실행 시간",
    )

    # SwarmResult의 3개 status 직접 노출 (정보 보존)
    context_status: str | None = Field(
        default=None, description="ContextAgent 상태: ok/empty/error/timeout",
    )
    planner_status: str | None = Field(
        default=None, description="PlannerAgent 상태: ok/fallback",
    )
    generator_status: str | None = Field(
        default=None, description="GeneratorAgent 상태: ok/fallback",
    )
    generator_finish_reason: str | None = Field(
        default=None, description="LLM finish_reason: stop/length/error",
    )
    generator_model_name: str | None = Field(
        default=None,
        description=(
            "GeneratorResult.model_name (실제 답변을 만든 모델/슬롯 model). "
            "정직성/관측용. 키 값이 아니라 모델 식별자만 노출한다."
        ),
    )

    plan_intent: str | None = Field(
        default=None,
        description="FinalPlan.intent: code_generation/analysis/creative/answer/general",
    )


class QueryResponse(BaseModel):
    trace_id: str
    answer: str
    path_taken: str = Field(
        description=(
            "Pipeline exit point: thalamus | exact_cache | semantic_cache | "
            "tier_1_5 | routed_lightweight | routed_standard | routed_full_pipeline."
        )
    )
    route_decision: RouteDecision | None = None
    difficulty: int | None = None
    category: str | None = None
    # Phase 3 STEP 3.2 — Epinephrine outcome (Optional: early-exit paths
    # leave these None; routed paths populate them).
    selected_tier: str | None = Field(
        default=None,
        description=(
            "ModelTier.name (e.g. 'DEEP_THINKING'). None on early-exit paths "
            "(thalamus / exact_cache / semantic_cache / tier_1_5)."
        ),
    )
    epinephrine_active: bool = False
    epinephrine_reason: str | None = None
    # Phase 4 STEP 3.3a — NeuroScope path 분석 + Swarm 통합 대비.
    response_source: ResponseSource | None = Field(
        default=None,
        description="최종 응답이 어디서 생성됐는지. NeuroScope path 분석용.",
    )
    # live LLM answer path — answer가 어디서 왔는지 + 시스템 LLM 모드.
    # 둘 다 additive·optional(기본 None) — 기존 스키마 비파괴. swarm 경로에서만 채워진다.
    answer_source: str | None = Field(
        default=None,
        description=(
            "swarm answer의 출처: 'generator'(실 생성 텍스트) | 'unavailable'(생성 실패 차단). "
            "early-exit(thalamus/cache/tier_1_5/glycine) 경로는 None."
        ),
    )
    llm_mode: str | None = Field(
        default=None,
        description=(
            "swarm 경로에서 사용된 시스템 LLM 모드: 'mock' | 'live'. "
            "early-exit 경로는 None(LLM 답변이 아님)."
        ),
    )
    swarm_trace: SwarmTrace | None = Field(
        default=None,
        description="Swarm 실행 추적. routed 경로에서만 채워짐 (STEP 3.3b 이후).",
    )
    # Phase 4 STEP 5.1 — Glycine pre-flight outcome
    glycine_active: bool = False
    glycine_reason: str | None = None
    glycine_action: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    # read-only 상태 노출 (additive·비파괴). demo readiness 가 그대로 중계한다.
    #   llm_mode   — 시스템 LLM 모드 'mock' | 'live' (app.state.llm_mode).
    #   slots_ready — 5칸 Tier Slot Registry 가 전부 필요한 키를 갖췄는지의 단일
    #                 집계 불리언 (slot_registry preflight 재사용). 칸별 상세·키
    #                 값·env 이름·벤더명은 절대 싣지 않는다.
    llm_mode: str | None = None
    slots_ready: bool | None = None

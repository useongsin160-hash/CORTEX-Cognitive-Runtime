from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.core.model_tier import ModelTier
from app.execution.context_models import ContextAgentResult

PLCStage = Literal[1, 2, 3]

# 7 fixed categories (Phase 2/3 — Synapse Layer in Phase 3.5 may extend).
Category = Literal[
    "coding",
    "game_design",
    "math_logic",
    "writing",
    "data_analysis",
    "system_design",
    "general",
]

CATEGORIES: tuple[Category, ...] = (
    "coding",
    "game_design",
    "math_logic",
    "writing",
    "data_analysis",
    "system_design",
    "general",
)

ClassificationMethod = Literal["centroid", "keyword_fallback"]


class Difficulty(IntEnum):
    # 5-stage scale, value-aligned 1:1 with ModelTier (1→LIGHTWEIGHT … 5→
    # DEEP_THINKING) so difficulty alone selects the model tier (B12). HARD=3
    # keeps its name but is now the *middle* rung (= ModelTier.STANDARD), not
    # the top — "high difficulty" is VERY_HARD/DEEP_THINKING (>=4).
    EASY = 1
    MEDIUM = 2
    HARD = 3
    VERY_HARD = 4
    DEEP_THINKING = 5


class AgentStatus(BaseModel):
    completed: bool = False
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)


class EvaluationResult(BaseModel):
    """Output of SemanticEvaluator. Sensor-only payload (no state writes
    — Synapse Layer in Phase 3.5 handles accumulation).

    Fields added in Phase 3 STEP 2:
      - similarity: raw cosine of the prompt embedding against the
        winning category centroid in the mean-centered frame.
        Theoretical range [-1.0, 1.0]; positive on healthy seed
        matches, can dip negative for genuinely unrelated prompts.
      - classification_method: which classifier produced `category`.
        "centroid" on the normal path; "keyword_fallback" when
        CentroidStore failed and the legacy keyword sieve took over
        (Graceful Fallback — never crash the request).
    """

    difficulty: int = Field(ge=1, le=5)
    category: Category
    embedding: list[float] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    similarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    classification_method: ClassificationMethod = "centroid"


class ContinuationContext(BaseModel):
    """Phase 5 STEP 5 — continuation cue + active_goal 순수 snapshot.

    ContinuationDetector가 채우며 routes.py → AsyncSwarm 흐름에 전달한다.
    Lock/Queue/Store/coroutine 객체 포함 금지 — 순수 Pydantic 데이터만 허용.
    """

    detected: bool = False
    cue_keyword: str | None = None
    cue_language: str | None = None
    active_goal_id: str | None = None
    active_goal_title: str | None = None
    active_goal_category: str | None = None
    active_goal_summary: str | None = None


class TaskContext(BaseModel):
    """Per-query state object created by LC.

    Pure JSON-serializable Pydantic model. MUST NOT carry locks, queues,
    or any non-serializable runtime object — those live in LockManager.

    Phase 3 STEP 3.2 adds the Epinephrine outcome:
      - `selected_tier` is the ModelTier IntEnum *internally*. Routes/API
        layer must surface this as ModelTier.name (string) — never let
        the integer leak into the public response.
    """

    trace_id: str
    # 원본 쿼리 + Evaluator 분류 카테고리 — Phase 4 STEP 2 Context Agent가
    # 검색에 사용. LC가 채운다. early-exit 경로에서는 기본값 유지.
    prompt: str = ""
    category: str | None = None
    difficulty: Difficulty = Difficulty.EASY
    # 노르에피네프린 활성 여부. LC가 difficulty>=4(VERY_HARD) 판정 시 True.
    # Generator Agent가 LLM 호출 직전 GenerationParams 변조에 사용.
    # Phase 4 STEP 1에서 Norepinephrine.modify_params()와 연결됨.
    ne_boost: bool = False
    plc_stage: PLCStage = 1
    context_agent: AgentStatus = Field(default_factory=AgentStatus)
    planner_agent: AgentStatus = Field(default_factory=AgentStatus)
    generator_agent: AgentStatus = Field(default_factory=AgentStatus)
    # 설계서 line 391 명세. Phase 3.5: Tier-1.5 miss 이후 LC가 채운다.
    # early-exit 경로(thalamus/cache/tier_1_5)에서는 빈 dict로 유지.
    # Phase 4 Context Agent가 ChromaDB 탐색 범위 결정에 사용 예정.
    # 순수 dict[str, float] — 락/큐/coroutine 객체 절대 포함 금지.
    synapse_snapshot: dict[str, float] = Field(default_factory=dict)
    epinephrine_active: bool = False
    selected_tier: ModelTier = ModelTier.STANDARD
    selected_model: str | None = None
    epinephrine_reason: str | None = None
    # Phase 4 STEP 2 Context Agent 검색 결과. STEP 3 swarm에서 활용.
    context_agent_result: ContextAgentResult | None = None
    # Phase 5 STEP 5 — Continuation cue + active_goal snapshot.
    # None 또는 detected=False면 normal path 의미.
    continuation_context: ContinuationContext | None = None
    # B11 S3b — final skip_router path AFTER the RPE override (routes sets this
    # post-override). The swarm reads it to wire execution to the band:
    # "lightweight" skips Context Agent retrieval. None on early-exit / pre-routing.
    route_path: str | None = None

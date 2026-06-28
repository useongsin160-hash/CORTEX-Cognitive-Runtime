"""Phase 5 STEP 3 — PrefrontalCortex: LLM-free cue hierarchy + memory reasoning.

Phase 5 STEP 5: cue 탐지 로직을 app.routing.cue_classifier로 분리.
PFC는 CueClassifier를 주입받아 사용한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

from app.api.schemas.context import EvaluationResult
from app.memory.session_goal_context import SessionGoalContext
from app.routing.cue_classifier import CueClassifier

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PFCCueType = Literal[
    "completion",
    "goal_creation",
    "continuation",
    "correction",
    "active_match",
    "embedding_match",
    "category_fallback",
    "general_fallback",
]

PFCIntent = Literal[
    "complete_goal",
    "create_goal",
    "continue_goal",
    "correct_goal",
    "match_active",
    "match_embedding",
    "category_hint",
    "general",
]

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoalSnapshot:
    """Immutable point-in-time snapshot of a Goal. Prevents PFC from mutating GoalStack."""

    goal_id: str
    title: str
    category: str | None
    priority: float
    source: str
    status: str
    summary: str | None = None
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class PFCHint:
    intent: PFCIntent
    cue_type: PFCCueType
    confidence: float
    matched_goal_id: str | None = None
    candidate_title: str | None = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"PFCHint.confidence must be in [0.0, 1.0], got {self.confidence}"
            )


@dataclass(frozen=True)
class GoalCandidate:
    title: str
    category: str | None = None
    source: str = "pfc_inferred"
    priority: float = 0.5
    summary: str | None = None


@dataclass(frozen=True)
class PFCDecision:
    hint: PFCHint
    new_goal_candidate: GoalCandidate | None = None
    matched_goal: GoalSnapshot | None = None


@dataclass(frozen=True)
class GoalStackSummary:
    active_goals: tuple[GoalSnapshot, ...]  # sorted by effective score desc
    top_goal: GoalSnapshot | None
    all_goals: tuple[GoalSnapshot, ...]
    depth: int


@dataclass(frozen=True)
class SessionGoalContextSummary:
    scope_id: str
    scope_type: str
    goal_stack_summary: GoalStackSummary
    last_active_goal_id: str | None = None


# ---------------------------------------------------------------------------
# Cue 탐지: app.routing.cue_classifier로 이전 (Phase 5 STEP 5)
# PFC는 CueClassifier 결과의 cue_type을 사용하여 8단계 사다리를 분기한다.
# ---------------------------------------------------------------------------

# Composite matching weights
_W_EMBED: Final[float] = 0.55
_W_KW: Final[float] = 0.25
_W_CAT: Final[float] = 0.15
_W_BONUS: Final[float] = 0.05

_W_KW_NOEMBED: Final[float] = 0.45
_W_CAT_NOEMBED: Final[float] = 0.35
_W_BONUS_NOEMBED: Final[float] = 0.20

_MATCH_THRESHOLD: Final[float] = 0.5

# ---------------------------------------------------------------------------
# Integration config (Phase 5 STEP 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PFCIntegrationConfig:
    """PFC ↔ Planner 통합 설정.

    Phase 5 STEP 4 임시값. Phase 6 RPE 또는 STEP 6 100쿼리 회귀 후 조정.
    """

    hint_timeout_ms: float = 30.0
    max_hint_timeout_ms: float = 50.0
    pfc_confidence_threshold: float = 0.7

    def __post_init__(self) -> None:
        if self.hint_timeout_ms <= 0:
            raise ValueError(
                f"hint_timeout_ms must be positive: {self.hint_timeout_ms}"
            )
        if self.max_hint_timeout_ms < self.hint_timeout_ms:
            raise ValueError(
                f"max_hint_timeout_ms ({self.max_hint_timeout_ms}) must be "
                f">= hint_timeout_ms ({self.hint_timeout_ms})"
            )
        if not (0.0 <= self.pfc_confidence_threshold <= 1.0):
            raise ValueError(
                f"pfc_confidence_threshold must be in [0.0, 1.0]: "
                f"{self.pfc_confidence_threshold}"
            )


# Cue type sets for the planner integration matrix
_STRONG_CUES: Final[frozenset[str]] = frozenset({
    "completion", "goal_creation", "continuation", "correction",
})
_CONDITIONAL_CUES: Final[frozenset[str]] = frozenset({
    "active_match", "embedding_match",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: tuple[float, ...]) -> float:
    """Cosine similarity clamped to [0.0, 1.0]. Negative cosine → 0."""
    if not a or not b:
        return 0.0
    dot = sum(ai * bi for ai, bi in zip(a, b))
    mag_a = math.sqrt(sum(ai * ai for ai in a))
    mag_b = math.sqrt(sum(bi * bi for bi in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (mag_a * mag_b)))


def _keyword_overlap(query: str, title: str, summary: str | None = None) -> float:
    """Jaccard-style word overlap in [0.0, 1.0]."""
    q_words = set(query.lower().split())
    target = title.lower() + (" " + summary.lower() if summary else "")
    t_words = set(target.split())
    if not q_words or not t_words:
        return 0.0
    union = q_words | t_words
    return len(q_words & t_words) / len(union)


def _category_match(q_cat: str | None, g_cat: str | None) -> float:
    if q_cat and g_cat and q_cat == g_cat:
        return 1.0
    return 0.0


def _score_goal(
    query: str,
    eval_result: EvaluationResult,
    goal: GoalSnapshot,
) -> float:
    """Composite match score using available signals."""
    kw = _keyword_overlap(query, goal.title, goal.summary)
    cat = _category_match(eval_result.category, goal.category)
    # bonus = 1.0 for user_explicit goals (getattr for safety if source is Enum)
    bonus = 1.0 if getattr(goal, "source", None) == "user_explicit" else 0.0
    embedding = eval_result.embedding
    goal_emb = goal.embedding
    if embedding and goal_emb:
        emb = _cosine(embedding, goal_emb)
        return _W_EMBED * emb + _W_KW * kw + _W_CAT * cat + _W_BONUS * bonus
    return _W_KW_NOEMBED * kw + _W_CAT_NOEMBED * cat + _W_BONUS_NOEMBED * bonus


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def make_goal_stack_summary(
    context: SessionGoalContext,
    embeddings: dict[str, tuple[float, ...]] | None = None,
) -> GoalStackSummary:
    """Build a GoalStackSummary snapshot from a SessionGoalContext.

    ``embeddings`` maps goal_id → precomputed embedding tuple.
    Active goals are ordered by effective score descending (mirrors GoalStack.get_active()).
    """
    emb = embeddings or {}

    def _snap(g: object) -> GoalSnapshot:
        return GoalSnapshot(
            goal_id=g.goal_id,  # type: ignore[attr-defined]
            title=g.title,  # type: ignore[attr-defined]
            category=g.category,  # type: ignore[attr-defined]
            priority=g.priority,  # type: ignore[attr-defined]
            source=getattr(g.source, "value", g.source),  # type: ignore[attr-defined]
            status=getattr(g.status, "value", g.status),  # type: ignore[attr-defined]
            summary=g.summary,  # type: ignore[attr-defined]
            embedding=emb.get(g.goal_id),  # type: ignore[attr-defined]
        )

    active_snaps = tuple(_snap(g) for g in context.goal_stack.get_active())
    all_snaps = tuple(_snap(g) for g in context.goal_stack.list_all())
    return GoalStackSummary(
        active_goals=active_snaps,
        top_goal=active_snaps[0] if active_snaps else None,
        all_goals=all_snaps,
        depth=len(context.goal_stack),
    )


# ---------------------------------------------------------------------------
# PrefrontalCortex
# ---------------------------------------------------------------------------


class PrefrontalCortex:
    """Phase 5 PFC: LLM-free 8-step cue ladder.

    Constraints:
    - No LLM calls.
    - No embedder instantiation (embeddings arrive via EvaluationResult).
    - No direct GoalStack mutation (reads GoalStackSummary snapshots).
    - Callers are responsible for asyncio.wait_for timeout wrapping.

    Phase 5 STEP 5: cue 매칭은 주입받은 CueClassifier에 위임한다.
    cue_classifier=None 시 default 인스턴스를 생성하므로 기존 코드 호환.
    """

    def __init__(self, cue_classifier: CueClassifier | None = None) -> None:
        self._cue_classifier = cue_classifier or CueClassifier()

    async def infer_hint(
        self,
        query: str,
        eval_result: EvaluationResult,
        goal_stack_summary: GoalStackSummary | None = None,
        active_goal: GoalSnapshot | None = None,
    ) -> PFCDecision:
        """8-step early-exit cue ladder. First matching step wins."""

        cue = self._cue_classifier.classify(query)

        # Step 1: completion — user signals the current goal is done.
        if active_goal is not None and cue.cue_type == "completion":
            return PFCDecision(
                hint=PFCHint(
                    intent="complete_goal",
                    cue_type="completion",
                    confidence=0.9,
                    matched_goal_id=active_goal.goal_id,
                ),
                matched_goal=active_goal,
            )

        # Step 2: goal_creation — explicit new-goal signal.
        if cue.cue_type == "goal_creation":
            candidate_title = query.strip()[:120]
            candidate_category = (
                eval_result.category if eval_result.category != "general" else None
            )
            return PFCDecision(
                hint=PFCHint(
                    intent="create_goal",
                    cue_type="goal_creation",
                    confidence=0.8,
                    candidate_title=candidate_title,
                ),
                new_goal_candidate=GoalCandidate(
                    title=candidate_title,
                    category=candidate_category,
                    source="pfc_inferred",
                    priority=0.5,
                ),
            )

        # Step 3: continuation — resume the current active goal.
        if active_goal is not None and cue.cue_type == "continuation":
            return PFCDecision(
                hint=PFCHint(
                    intent="continue_goal",
                    cue_type="continuation",
                    confidence=0.85,
                    matched_goal_id=active_goal.goal_id,
                ),
                matched_goal=active_goal,
            )

        # Step 4: correction — fix something in the current active goal.
        if active_goal is not None and cue.cue_type == "correction":
            return PFCDecision(
                hint=PFCHint(
                    intent="correct_goal",
                    cue_type="correction",
                    confidence=0.75,
                    matched_goal_id=active_goal.goal_id,
                ),
                matched_goal=active_goal,
            )

        # Step 5: active_match — composite scoring across active goals.
        if goal_stack_summary is not None and goal_stack_summary.active_goals:
            best_goal: GoalSnapshot | None = None
            best_score = 0.0
            for goal in goal_stack_summary.active_goals:
                score = _score_goal(query, eval_result, goal)
                if score > best_score:
                    best_score = score
                    best_goal = goal
            if best_score >= _MATCH_THRESHOLD and best_goal is not None:
                return PFCDecision(
                    hint=PFCHint(
                        intent="match_active",
                        cue_type="active_match",
                        confidence=min(1.0, best_score),
                        matched_goal_id=best_goal.goal_id,
                    ),
                    matched_goal=best_goal,
                )

        # Step 6: embedding_match — cosine-only scan across ALL goals.
        if goal_stack_summary is not None and eval_result.embedding:
            best_goal = None
            best_score = 0.0
            for goal in goal_stack_summary.all_goals:
                if goal.embedding:
                    score = _cosine(eval_result.embedding, goal.embedding)
                    if score > best_score:
                        best_score = score
                        best_goal = goal
            if best_score >= _MATCH_THRESHOLD and best_goal is not None:
                return PFCDecision(
                    hint=PFCHint(
                        intent="match_embedding",
                        cue_type="embedding_match",
                        confidence=min(1.0, best_score),
                        matched_goal_id=best_goal.goal_id,
                    ),
                    matched_goal=best_goal,
                )

        # Step 7: category_fallback — only for specific (non-"general") categories.
        if eval_result.category != "general":
            return PFCDecision(
                hint=PFCHint(
                    intent="category_hint",
                    cue_type="category_fallback",
                    confidence=min(1.0, eval_result.confidence * 0.6),
                )
            )

        # Step 8: general_fallback.
        return PFCDecision(
            hint=PFCHint(
                intent="general",
                cue_type="general_fallback",
                confidence=0.1,
            )
        )

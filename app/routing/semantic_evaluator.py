"""Layer 2 — Semantic Evaluator (sequence step 5).

**Sensor only.** Classifies difficulty (1~5) and category, never stores
state — state accumulation lives in the Synapse Layer (Phase 3.5).

Phase 3 STEP 2: category classification now uses CentroidStore vector
clustering. The legacy keyword sieve is preserved as the Graceful
Fallback path: if the CentroidStore raises or has not been built yet
the evaluator falls back to keyword matching so /query never crashes
on classifier outages (design doc Graceful Fallback section).

Difficulty heuristic is intentionally unchanged in STEP 2 —
Phase 3.5 will revisit it once Synapse weights exist.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.api.schemas.context import (
    CATEGORIES,
    Category,
    EvaluationResult,
)
from app.core.logging import get_spinal_logger

if TYPE_CHECKING:
    from app.routing.centroid_store import CentroidStore

# Re-export for backwards compatibility with Phase 2 callers (lc.py,
# pfc_stub.py, tests/phase2/test_routing.py) that still import these
# names from `app.routing.semantic_evaluator`.
__all__ = ["CATEGORIES", "Category", "EvaluationResult", "SemanticEvaluator"]


# ── Legacy keyword sieve (fallback only) ─────────────────────────────────
# Retained per the Graceful Fallback design rule: if CentroidStore can't
# answer, we degrade to the Phase 2 keyword classifier rather than
# crashing the request.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding": (
        "def ", "class ", "function", "bug", "error", "exception", "stack trace",
        "python", "javascript", "typescript", "compile",
        "코드", "디버깅", "리팩토링", "함수",
    ),
    "game_design": (
        "game", "level", "npc", "quest", "boss", "mechanic",
        "게임", "퀘스트", "레벨", "보스", "기획",
    ),
    "math_logic": (
        "solve", "prove", "theorem", "equation", "integral", "derivative",
        "logic", "algorithm", "complexity", "big-o",
        "수학", "증명", "방정식", "알고리즘", "복잡도",
    ),
    "writing": (
        "write", "essay", "story", "translate", "draft", "summary",
        "글", "번역", "작성", "에세이", "초안", "요약",
    ),
    "data_analysis": (
        "data", "csv", "stats", "regression", "histogram", "outlier",
        "데이터", "통계", "분석", "회귀", "시각화",
    ),
    "system_design": (
        "architecture", "design", "infra", "scalability", "throughput", "latency",
        "아키텍처", "설계", "인프라", "확장성", "처리량",
    ),
}

# Difficulty heuristics — UNCHANGED from Phase 2.
# PHASE 3.5: Synapse 통합 시 임베딩 기반 난이도 분류로 전환 예정.
_HARD_KEYWORDS: tuple[str, ...] = (
    "prove", "design", "architecture", "trade-off", "trade off",
    "optimize", "optimisation", "optimization",
    "복잡도", "최적화", "증명", "설계해", "아키텍처",
)
_MEDIUM_KEYWORDS: tuple[str, ...] = (
    "how", "why", "compare", "explain",
    "어떻게", "왜", "비교", "설명",
)


def _keyword_category(prompt: str) -> Category:
    lowered = prompt.lower()
    scores: dict[str, int] = {c: 0 for c in CATEGORIES}
    for cat, kws in _CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in kws if kw in lowered)
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "general"  # type: ignore[return-value]


def _keyword_confidence(prompt: str, category: Category) -> float:
    if category == "general":
        return 0.3
    lowered = prompt.lower()
    hits = sum(1 for kw in _CATEGORY_KEYWORDS.get(category, ()) if kw in lowered)
    return min(1.0, hits / 5.0) if hits > 0 else 0.3


def _compute_difficulty(prompt: str) -> int:
    """Heuristic 5-stage difficulty (1~5), aligned with the Difficulty enum.

    Monotone in two signals — keyword tier and length — so a harder prompt
    never scores lower. EASY=1 is preserved exactly (trivial/short prompts)
    because Tier-1.5 augmentation keys on difficulty==EASY; widening the scale
    must not pull short prompts out of that band. PHASE 3.5 will replace this
    with embedding-based scoring once Synapse weights exist.
    """
    lowered = prompt.lower()
    word_count = len(prompt.split())
    has_hard = any(k in lowered for k in _HARD_KEYWORDS)
    has_medium = any(k in lowered for k in _MEDIUM_KEYWORDS)
    if has_hard:
        # Hard signal → VERY_HARD; a long hard prompt escalates to DEEP_THINKING.
        return 5 if word_count > 60 else 4
    if has_medium or word_count > 20:
        # Medium signal → MEDIUM; a strong medium signal reaches HARD (STANDARD).
        return 3 if (has_medium and word_count > 20) or word_count > 40 else 2
    return 1


class SemanticEvaluator:
    """Centroid-based classifier with keyword fallback."""

    def __init__(self, centroid_store: "CentroidStore | None" = None) -> None:
        # Optional injection. When None (legacy/unit-test construction),
        # the evaluator runs in keyword-only mode — every call is reported
        # as classification_method="keyword_fallback".
        self._centroid_store = centroid_store

    async def evaluate(
        self,
        prompt: str,
        trace_id: str | None = None,
    ) -> EvaluationResult:
        difficulty = _compute_difficulty(prompt)

        category, similarity, embedding, method = await self._classify(prompt, trace_id)

        if method == "centroid":
            # Centroid cosine in centered space is a reasonable confidence
            # proxy: map similarity from [-1, 1] to [0, 1].
            confidence = max(0.0, min(1.0, (similarity + 1.0) / 2.0))
        else:
            confidence = _keyword_confidence(prompt, category)

        return EvaluationResult(
            difficulty=difficulty,
            category=category,
            embedding=embedding,
            confidence=confidence,
            similarity=similarity,
            classification_method=method,
        )

    async def _classify(
        self,
        prompt: str,
        trace_id: str | None,
    ) -> tuple[Category, float, list[float], str]:
        if self._centroid_store is None:
            return _keyword_category(prompt), 0.0, [], "keyword_fallback"
        try:
            raw_emb = await self._centroid_store.embed_text(prompt)
            category, similarity = await self._centroid_store.find_nearest(
                raw_emb, clamp=False,
            )
            return category, float(similarity), raw_emb.tolist(), "centroid"  # type: ignore[return-value]
        except Exception as exc:
            if trace_id is not None:
                await get_spinal_logger().log_event(
                    trace_id=trace_id,
                    module_name="routing.semantic_evaluator",
                    event_type="evaluator.fallback",
                    payload={"reason": type(exc).__name__, "detail": str(exc)[:200]},
                )
            return _keyword_category(prompt), 0.0, [], "keyword_fallback"

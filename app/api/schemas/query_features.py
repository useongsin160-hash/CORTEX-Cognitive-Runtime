"""QueryFeatures — 쿼리 단위 누적 feature 데이터 (ADR-002 자리 마련)."""
from __future__ import annotations

from pydantic import BaseModel


class QueryFeatures(BaseModel):
    """쿼리에 대해 누적된 feature 데이터.

    Phase 4 STEP 1: 구조만 도입. 실제 임베딩 공유 연결은 STEP 2
    Context Agent 작업.

    의도 (ADR-002): SemanticCache, SemanticEvaluator, ContextAgent가
    같은 임베딩을 공유하여 중복 계산을 제거한다. 현재는 자리만 마련.

    PHASE 4 STEP 2: 실제 임베딩 공유 연결.
    """

    raw_query: str
    normalized_query: str | None = None
    embedding: list[float] | None = None  # ADR-002 공유 슬롯
    category: str | None = None
    difficulty: int | None = None
    similarity: float | None = None

    # 디버깅용 — "semantic_cache" / "evaluator" / "context_agent"
    embedding_source: str | None = None

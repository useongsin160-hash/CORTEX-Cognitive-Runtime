"""Context Agent — ChromaDB 검색 + GABA 필터링 (ADR-004 규약).

원칙 1: 검색/retrieval만 담당. 요약/생성 금지 (STEP 3 영역).
원칙 10: SynapseStore 직접 접근 금지 — TaskContext.synapse_snapshot
dict만 참조한다 (snapshot은 LC.apply_snapshot 시점에 dict로 추출됨).
"""
from __future__ import annotations

from app.api.schemas.context import TaskContext
from app.api.schemas.query_features import QueryFeatures
from app.execution.category_selector import CategorySelector
from app.execution.chromadb_searcher import ChromaDBSearcher
from app.execution.context_models import ContextAgentResult
from app.execution.gaba import GABAFilter

# B11 S3b-promote: Epinephrine limit-break threshold. Lower than the default 0.4
# so weak-relevance categories (weight 0.2~0.4) also pass → broader ChromaDB
# scope. Bounded floor (never 0 = no unbounded scrape); ≤7 categories total.
LIMIT_BREAK_THRESHOLD: float = 0.2


class ContextAgent:
    """ADR-004 규약 준수 컨텍스트 검색기.

    순서: snapshot 참조 → CategorySelector → ChromaDBSearcher →
    GABAFilter → ContextAgentResult (순수 Pydantic).
    """

    def __init__(
        self,
        selector: CategorySelector,
        searcher: ChromaDBSearcher,
        gaba: GABAFilter,
    ) -> None:
        # 생성자 인자는 selector/searcher/gaba 3개뿐 — SynapseStore
        # 인스턴스를 받지 않는다 (원칙 10).
        self._selector = selector
        self._searcher = searcher
        self._gaba = gaba

    async def retrieve(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None = None,
    ) -> ContextAgentResult:
        """ADR-004 규약 검색. query_features.embedding이 있으면 재사용."""
        # 1. Synapse snapshot (없으면 빈 dict — early-exit 경로).
        synapse_snapshot = task_context.synapse_snapshot or {}
        evaluator_category = task_context.category or "general"

        # 2. 카테고리 선택 (가중치 정렬 + threshold + fallback).
        # B11 S3b-promote: full_pipeline 진입 시 에피네프린 active → limit-break.
        # threshold를 0.2로 낮춰 조회 범위만 유계 확장(read-only — store write 0).
        limit_break = bool(getattr(task_context, "epinephrine_active", False))
        selected_categories, fallback_used = self._selector.select(
            synapse_snapshot=synapse_snapshot,
            evaluator_category=evaluator_category,
            threshold=LIMIT_BREAK_THRESHOLD if limit_break else None,
        )

        # 3. ADR-002 — 임베딩 재사용 시도.
        query_embedding: list[float] | None = None
        embedding_source: str | None = None
        if query_features is not None and query_features.embedding is not None:
            query_embedding = query_features.embedding
            embedding_source = "query_features"

        # 4. ChromaDB 검색.
        query_text = (
            query_features.raw_query if query_features is not None
            else (task_context.prompt or "")
        )
        retrieved = await self._searcher.search(
            query=query_text,
            selected_categories=selected_categories,
            query_embedding=query_embedding,
        )
        if embedding_source is None and retrieved:
            embedding_source = "context_agent"

        # 5. GABA 필터.
        filtered, gaba_fallback = self._gaba.filter(retrieved)
        filtered_count = sum(1 for ctx in filtered if ctx.masked_by_gaba)

        return ContextAgentResult(
            selected_categories=selected_categories,
            retrieved=filtered,
            filtered_count=filtered_count,
            fallback_used=fallback_used,
            gaba_fallback_used=gaba_fallback,
            embedding_source=embedding_source,
        )

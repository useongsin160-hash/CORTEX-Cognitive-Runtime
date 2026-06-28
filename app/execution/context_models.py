"""Context Agent 데이터 모델 — 순수 Pydantic (DB 객체 포함 금지)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievedContext(BaseModel):
    """ChromaDB에서 검색된 단일 청크.

    순수 데이터. DB client / collection / coroutine 객체 포함 금지.
    """

    chunk_id: str
    text: str
    category: str | None = None
    similarity: float  # cosine similarity (mean-centered 좌표계, [-1, 1])
    source: str | None = None
    masked_by_gaba: bool = False  # GABA 필터로 노이즈 표시 여부


class ContextAgentResult(BaseModel):
    """Context Agent 검색 결과.

    TaskContext에 탑재 가능한 순수 Pydantic 모델. JSON 직렬화 안전.
    """

    selected_categories: list[str] = Field(default_factory=list)
    retrieved: list[RetrievedContext] = Field(default_factory=list)
    filtered_count: int = 0          # GABA로 masked된 개수
    fallback_used: bool = False      # threshold 0개 → evaluator category 사용
    gaba_fallback_used: bool = False # 전부 masked → top-1 보존

    # 디버깅용 — "query_features" / "context_agent"
    embedding_source: str | None = None

"""ChromaDB metadata-filter 검색 — 단일 컬렉션 + category 필터."""
from __future__ import annotations

import asyncio

from app.execution.context_models import RetrievedContext


class ChromaDBSearcher:
    """ChromaDB metadata filter 기반 카테고리 검색.

    원칙 7: 단일 컬렉션 유지 — 카테고리별 컬렉션 분리 금지.
    metadata.category 필드로 $eq / $in 필터링.

    embedder는 앱 공유 임베딩 함수 (app.core.embedder.get_embedding_function
    의 callable, list[str] -> list[list[float]]). ADR-002: query_embedding이
    제공되면 재계산하지 않는다.
    """

    def __init__(self, collection, embedder) -> None:
        # collection: chromadb.Collection (lifespan에서 주입, STEP 3)
        # embedder: callable[list[str]] -> list[list[float]] (공유 임베더)
        self._collection = collection
        self._embedder = embedder

    async def search(
        self,
        query: str,
        selected_categories: list[str],
        query_embedding: list[float] | None = None,
        n_results: int = 10,
    ) -> list[RetrievedContext]:
        """Return RetrievedContext list ordered by similarity desc.

        query_embedding이 주어지면 임베딩 재계산을 건너뛴다 (ADR-002).
        """
        if query_embedding is None:
            vectors = await asyncio.to_thread(self._embedder, [query])
            query_embedding = vectors[0]
            if hasattr(query_embedding, "tolist"):
                query_embedding = query_embedding.tolist()

        # 단일 카테고리 → $eq, 복수 → $in.
        if len(selected_categories) == 1:
            where_clause = {"category": selected_categories[0]}
        else:
            where_clause = {"category": {"$in": selected_categories}}

        results = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause,
            include=["documents", "metadatas", "distances"],
        )

        retrieved: list[RetrievedContext] = []
        ids = results.get("ids") or [[]]
        if ids[0]:
            for i, chunk_id in enumerate(ids[0]):
                doc = results["documents"][0][i]
                meta = results["metadatas"][0][i] or {}
                distance = results["distances"][0][i]
                retrieved.append(RetrievedContext(
                    chunk_id=chunk_id,
                    text=doc,
                    category=meta.get("category"),
                    similarity=1.0 - float(distance),  # cosine distance → similarity
                    source=meta.get("source"),
                    masked_by_gaba=False,
                ))

        retrieved.sort(key=lambda c: c.similarity, reverse=True)
        return retrieved

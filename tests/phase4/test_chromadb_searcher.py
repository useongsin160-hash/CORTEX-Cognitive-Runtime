"""Phase 4 STEP 2 — ChromaDBSearcher (metadata filter + embedding reuse)."""
from __future__ import annotations

import pytest

from app.execution.chromadb_searcher import ChromaDBSearcher


class _MockCollection:
    """Records the last query() call and returns a canned result."""

    def __init__(self, result: dict) -> None:
        self._result = result
        self.last_where: dict | None = None
        self.last_embeddings = None

    def query(self, query_embeddings, n_results, where, include):
        self.last_where = where
        self.last_embeddings = query_embeddings
        return self._result


class _MockEmbedder:
    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, texts):
        self.call_count += 1
        return [[0.11, 0.22, 0.33] for _ in texts]


_CANNED = {
    "ids": [["c1", "c2"]],
    "documents": [["doc one", "doc two"]],
    "metadatas": [[{"category": "coding", "source": "s1"},
                   {"category": "coding", "source": "s2"}]],
    "distances": [[0.1, 0.6]],
}


@pytest.mark.asyncio
async def test_single_category_uses_eq_filter():
    collection = _MockCollection(_CANNED)
    searcher = ChromaDBSearcher(collection, _MockEmbedder())
    await searcher.search("q", ["coding"], query_embedding=[0.1, 0.2])
    assert collection.last_where == {"category": "coding"}


@pytest.mark.asyncio
async def test_multiple_categories_use_in_filter():
    collection = _MockCollection(_CANNED)
    searcher = ChromaDBSearcher(collection, _MockEmbedder())
    await searcher.search("q", ["coding", "writing"], query_embedding=[0.1, 0.2])
    assert collection.last_where == {"category": {"$in": ["coding", "writing"]}}


@pytest.mark.asyncio
async def test_provided_embedding_skips_embedder():
    embedder = _MockEmbedder()
    searcher = ChromaDBSearcher(_MockCollection(_CANNED), embedder)
    await searcher.search("q", ["coding"], query_embedding=[0.5, 0.6])
    assert embedder.call_count == 0


@pytest.mark.asyncio
async def test_missing_embedding_calls_embedder():
    embedder = _MockEmbedder()
    searcher = ChromaDBSearcher(_MockCollection(_CANNED), embedder)
    await searcher.search("q", ["coding"], query_embedding=None)
    assert embedder.call_count == 1


@pytest.mark.asyncio
async def test_similarity_is_one_minus_distance():
    searcher = ChromaDBSearcher(_MockCollection(_CANNED), _MockEmbedder())
    results = await searcher.search("q", ["coding"], query_embedding=[0.1])
    by_id = {r.chunk_id: r for r in results}
    assert by_id["c1"].similarity == pytest.approx(0.9)   # 1 - 0.1
    assert by_id["c2"].similarity == pytest.approx(0.4)   # 1 - 0.6


@pytest.mark.asyncio
async def test_results_sorted_by_similarity_desc():
    searcher = ChromaDBSearcher(_MockCollection(_CANNED), _MockEmbedder())
    results = await searcher.search("q", ["coding"], query_embedding=[0.1])
    sims = [r.similarity for r in results]
    assert sims == sorted(sims, reverse=True)


@pytest.mark.asyncio
async def test_empty_result_returns_empty_list():
    empty = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    searcher = ChromaDBSearcher(_MockCollection(empty), _MockEmbedder())
    results = await searcher.search("q", ["coding"], query_embedding=[0.1])
    assert results == []

"""Phase 4 STEP 2 — ContextAgent.retrieve() end-to-end."""
from __future__ import annotations

import json

import pytest

from app.api.schemas.context import TaskContext
from app.api.schemas.query_features import QueryFeatures
from app.execution.category_selector import CategorySelector
from app.execution.chromadb_searcher import ChromaDBSearcher
from app.execution.context_agent import ContextAgent
from app.execution.gaba import GABAFilter


class _MockCollection:
    def __init__(self, result: dict) -> None:
        self._result = result
        self.last_where: dict | None = None

    def query(self, query_embeddings, n_results, where, include):
        self.last_where = where
        return self._result


class _MockEmbedder:
    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, texts):
        self.call_count += 1
        return [[0.1, 0.2, 0.3] for _ in texts]


def _result(distances: list[float]) -> dict:
    n = len(distances)
    return {
        "ids": [[f"c{i}" for i in range(n)]],
        "documents": [[f"doc {i}" for i in range(n)]],
        "metadatas": [[{"category": "coding", "source": f"s{i}"} for i in range(n)]],
        "distances": [distances],
    }


def _agent(collection, embedder) -> ContextAgent:
    return ContextAgent(
        selector=CategorySelector(),
        searcher=ChromaDBSearcher(collection, embedder),
        gaba=GABAFilter(),
    )


@pytest.mark.asyncio
async def test_retrieve_with_empty_snapshot_uses_evaluator_category():
    agent = _agent(_MockCollection(_result([0.1, 0.2])), _MockEmbedder())
    ctx = TaskContext(trace_id="t", prompt="debug this", category="coding")
    result = await agent.retrieve(ctx)
    assert result.selected_categories == ["coding"]
    assert result.fallback_used is True


@pytest.mark.asyncio
async def test_retrieve_with_snapshot_selects_weighted_categories():
    agent = _agent(_MockCollection(_result([0.1])), _MockEmbedder())
    ctx = TaskContext(
        trace_id="t", prompt="q", category="general",
        synapse_snapshot={"coding": 0.8, "writing": 0.2, "general": 0.5},
    )
    result = await agent.retrieve(ctx)
    assert result.selected_categories == ["coding", "general"]
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_retrieve_gaba_all_masked_keeps_top_1():
    # distances 0.7/0.8/0.9 → similarity 0.3/0.2/0.1, all below 0.5
    agent = _agent(_MockCollection(_result([0.7, 0.8, 0.9])), _MockEmbedder())
    ctx = TaskContext(trace_id="t", prompt="q", category="coding")
    result = await agent.retrieve(ctx)
    assert result.gaba_fallback_used is True
    unmasked = [c for c in result.retrieved if not c.masked_by_gaba]
    assert len(unmasked) == 1


@pytest.mark.asyncio
async def test_retrieve_reuses_query_features_embedding():
    embedder = _MockEmbedder()
    agent = _agent(_MockCollection(_result([0.1])), embedder)
    ctx = TaskContext(trace_id="t", prompt="q", category="coding")
    qf = QueryFeatures(raw_query="q", embedding=[0.9, 0.8, 0.7])
    result = await agent.retrieve(ctx, query_features=qf)
    assert embedder.call_count == 0  # ADR-002 reuse — no re-embed
    assert result.embedding_source == "query_features"


@pytest.mark.asyncio
async def test_retrieve_computes_embedding_when_absent():
    embedder = _MockEmbedder()
    agent = _agent(_MockCollection(_result([0.1])), embedder)
    ctx = TaskContext(trace_id="t", prompt="q", category="coding")
    result = await agent.retrieve(ctx)
    assert embedder.call_count == 1
    assert result.embedding_source == "context_agent"


@pytest.mark.asyncio
async def test_result_is_pure_pydantic_json_safe():
    agent = _agent(_MockCollection(_result([0.1, 0.2])), _MockEmbedder())
    ctx = TaskContext(trace_id="t", prompt="q", category="coding")
    result = await agent.retrieve(ctx)
    # model_dump_json must not raise — no DB client / coroutine leaked in.
    dumped = json.loads(result.model_dump_json())
    assert "retrieved" in dumped
    assert "selected_categories" in dumped

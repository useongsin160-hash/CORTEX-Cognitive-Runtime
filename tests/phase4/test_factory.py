"""Phase 4 STEP 3.3b — build_execution_swarm factory."""
from __future__ import annotations

import pytest

from app.execution.factory import build_execution_swarm
from app.execution.mock_llm_client import MockLLMClient
from app.execution.swarm import AsyncSwarm
from app.routing.neuromodulators import Norepinephrine


class _MockCollection:
    def query(self, query_embeddings, n_results, where, include):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


class _MockEmbedder:
    def __call__(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_build_returns_async_swarm():
    swarm = build_execution_swarm(
        chroma_collection=_MockCollection(),
        embedder=_MockEmbedder(),
        llm_client=MockLLMClient(),
        norepinephrine=Norepinephrine(),
    )
    assert isinstance(swarm, AsyncSwarm)


def test_default_context_timeout_is_five_seconds():
    swarm = build_execution_swarm(
        chroma_collection=_MockCollection(),
        embedder=_MockEmbedder(),
        llm_client=MockLLMClient(),
        norepinephrine=Norepinephrine(),
    )
    assert swarm._context_timeout == 5.0


def test_custom_context_timeout_is_honored():
    swarm = build_execution_swarm(
        chroma_collection=_MockCollection(),
        embedder=_MockEmbedder(),
        llm_client=MockLLMClient(),
        norepinephrine=Norepinephrine(),
        context_timeout=2.5,
    )
    assert swarm._context_timeout == 2.5


@pytest.mark.asyncio
async def test_assembled_swarm_executes_end_to_end():
    """조립된 swarm으로 execute() 한 번 — 의존성 그래프 정상 작동 검증."""
    from app.api.schemas.context import TaskContext
    from app.core.model_tier import ModelTier

    swarm = build_execution_swarm(
        chroma_collection=_MockCollection(),
        embedder=_MockEmbedder(),
        llm_client=MockLLMClient(),
        norepinephrine=Norepinephrine(),
    )
    task = TaskContext(
        trace_id="t", prompt="hello world", category="general",
        selected_tier=ModelTier.STANDARD,
    )
    result = await swarm.execute(task)
    # ChromaDB 컬렉션은 비어있으므로 empty context → status "empty".
    assert result.context_status == "empty"
    # Generator는 정상 (Mock).
    assert result.generator_result.finish_reason == "stop"

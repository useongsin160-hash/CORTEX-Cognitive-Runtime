"""Phase 4 STEP 2 — context models JSON-safety."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.execution.context_models import ContextAgentResult, RetrievedContext


def test_retrieved_context_json_serializable():
    ctx = RetrievedContext(chunk_id="c1", text="hello", similarity=0.8)
    dumped = json.loads(ctx.model_dump_json())
    assert dumped["chunk_id"] == "c1"
    assert dumped["masked_by_gaba"] is False


def test_context_agent_result_json_serializable():
    result = ContextAgentResult(
        selected_categories=["coding"],
        retrieved=[RetrievedContext(chunk_id="c1", text="t", similarity=0.7)],
        filtered_count=0,
        fallback_used=False,
        gaba_fallback_used=False,
    )
    dumped = json.loads(result.model_dump_json())
    assert dumped["selected_categories"] == ["coding"]
    assert len(dumped["retrieved"]) == 1


def test_context_agent_result_defaults():
    result = ContextAgentResult()
    assert result.selected_categories == []
    assert result.retrieved == []
    assert result.filtered_count == 0
    assert result.fallback_used is False
    assert result.gaba_fallback_used is False
    assert result.embedding_source is None


def test_non_serializable_object_rejected():
    """A coroutine/lock object must not survive into the model."""
    import asyncio

    async def _coro():
        return 1

    coro = _coro()
    try:
        with pytest.raises(ValidationError):
            RetrievedContext(chunk_id="c1", text="t", similarity=coro)  # type: ignore[arg-type]
    finally:
        coro.close()

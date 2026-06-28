"""Phase 3 STEP 2 — Graceful Fallback under CentroidStore failure.

If `CentroidStore.embed_text` or `find_nearest` raises, the evaluator
must degrade to the legacy keyword classifier so /query never crashes
on classifier outages (design doc Graceful Fallback section).
"""
from __future__ import annotations

import pytest

from app.core.logging import get_spinal_logger
from app.routing.semantic_evaluator import SemanticEvaluator


class _ExplodingCentroidStore:
    """Mock CentroidStore whose every method raises a synthetic failure."""

    async def embed_text(self, text: str):
        raise RuntimeError("synthetic centroid embedder outage")

    async def find_nearest(self, embedding, clamp: bool = True):
        raise RuntimeError("synthetic find_nearest outage")


@pytest.mark.asyncio
async def test_fallback_when_centroid_raises():
    evaluator = SemanticEvaluator(centroid_store=_ExplodingCentroidStore())  # type: ignore[arg-type]
    result = await evaluator.evaluate(
        "please debug this python function that throws KeyError"
    )
    # Result must still be a usable EvaluationResult, not an exception.
    assert result.classification_method == "keyword_fallback"
    assert result.category == "coding"  # keyword sieve catches "python" / "function"
    assert 1 <= result.difficulty <= 3


@pytest.mark.asyncio
async def test_fallback_logs_warning_when_trace_id_given():
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    evaluator = SemanticEvaluator(centroid_store=_ExplodingCentroidStore())  # type: ignore[arg-type]
    await evaluator.evaluate(
        "write a short summary of yesterday's standup",
        trace_id=trace_id,
    )
    events = logger.get_trace(trace_id)
    event_types = [e.event_type for e in events]
    assert "evaluator.fallback" in event_types
    fallback_event = next(e for e in events if e.event_type == "evaluator.fallback")
    assert "reason" in fallback_event.payload


@pytest.mark.asyncio
async def test_fallback_silent_without_trace_id():
    """No trace_id supplied → no spinal write (evaluator stays pure)."""
    logger = get_spinal_logger()
    before = sum(len(v) for v in logger._traces.values())  # type: ignore[attr-defined]
    evaluator = SemanticEvaluator(centroid_store=_ExplodingCentroidStore())  # type: ignore[arg-type]
    result = await evaluator.evaluate("plain text without a trace id")
    after = sum(len(v) for v in logger._traces.values())  # type: ignore[attr-defined]
    assert after == before, "evaluator emitted spinal event without a trace_id"
    assert result.classification_method == "keyword_fallback"


@pytest.mark.asyncio
async def test_no_centroid_store_means_keyword_path():
    """Legacy construction (Phase 2 unit tests) — no store injected."""
    evaluator = SemanticEvaluator()  # no centroid store
    result = await evaluator.evaluate("solve this algorithm complexity problem")
    assert result.classification_method == "keyword_fallback"
    assert result.category == "math_logic"
    # Similarity is 0.0 on the keyword path — no embedding measurement.
    assert result.similarity == 0.0
    assert result.embedding == []

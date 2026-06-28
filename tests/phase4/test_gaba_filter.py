"""Phase 4 STEP 2 — GABAFilter (soft mask + top-1 fallback)."""
from __future__ import annotations

from app.execution.context_models import RetrievedContext
from app.execution.gaba import GABAFilter


def _ctx(cid: str, sim: float) -> RetrievedContext:
    return RetrievedContext(chunk_id=cid, text=f"text-{cid}", similarity=sim)


def test_empty_list():
    filtered, fallback = GABAFilter().filter([])
    assert filtered == []
    assert fallback is False


def test_all_above_threshold_none_masked():
    contexts = [_ctx("a", 0.9), _ctx("b", 0.7), _ctx("c", 0.55)]
    filtered, fallback = GABAFilter().filter(contexts)
    assert all(not c.masked_by_gaba for c in filtered)
    assert fallback is False


def test_all_below_threshold_keeps_top_1():
    contexts = [_ctx("a", 0.4), _ctx("b", 0.3), _ctx("c", 0.1)]
    filtered, fallback = GABAFilter().filter(contexts)
    assert fallback is True
    # top-1 (index 0, highest similarity) preserved
    assert filtered[0].masked_by_gaba is False
    assert filtered[1].masked_by_gaba is True
    assert filtered[2].masked_by_gaba is True


def test_mixed_partial_mask():
    contexts = [_ctx("a", 0.8), _ctx("b", 0.3), _ctx("c", 0.6)]
    filtered, fallback = GABAFilter().filter(contexts)
    assert fallback is False
    by_id = {c.chunk_id: c for c in filtered}
    assert by_id["a"].masked_by_gaba is False
    assert by_id["b"].masked_by_gaba is True
    assert by_id["c"].masked_by_gaba is False


def test_threshold_boundary_inclusive():
    contexts = [_ctx("a", 0.5)]
    filtered, fallback = GABAFilter().filter(contexts)
    # similarity == 0.5 → accepted (>= threshold)
    assert filtered[0].masked_by_gaba is False
    assert fallback is False


def test_negative_similarity_masked():
    contexts = [_ctx("a", 0.9), _ctx("b", -0.2)]
    filtered, fallback = GABAFilter().filter(contexts)
    by_id = {c.chunk_id: c for c in filtered}
    assert by_id["b"].masked_by_gaba is True
    assert fallback is False


def test_filter_does_not_mutate_input():
    contexts = [_ctx("a", 0.2)]
    GABAFilter().filter(contexts)
    # original object untouched (model_copy used)
    assert contexts[0].masked_by_gaba is False

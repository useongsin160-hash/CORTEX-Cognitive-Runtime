"""Phase 4 STEP 1 — QueryFeatures structure (ADR-002 placeholder)."""
from __future__ import annotations

from app.api.schemas.query_features import QueryFeatures


def test_minimal_construction():
    qf = QueryFeatures(raw_query="hello")
    assert qf.raw_query == "hello"


def test_all_optional_fields_default_none():
    qf = QueryFeatures(raw_query="hello")
    assert qf.normalized_query is None
    assert qf.embedding is None
    assert qf.category is None
    assert qf.difficulty is None
    assert qf.similarity is None
    assert qf.embedding_source is None


def test_embedding_slot_accepts_vector():
    qf = QueryFeatures(raw_query="hello", embedding=[0.1, 0.2, 0.3])
    assert qf.embedding == [0.1, 0.2, 0.3]


def test_lazy_fill_is_possible():
    qf = QueryFeatures(raw_query="hello")
    qf.category = "coding"
    qf.difficulty = 3
    qf.embedding_source = "evaluator"
    assert qf.category == "coding"
    assert qf.difficulty == 3
    assert qf.embedding_source == "evaluator"

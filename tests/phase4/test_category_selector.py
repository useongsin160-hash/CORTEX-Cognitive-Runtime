"""Phase 4 STEP 2 — CategorySelector (Synapse threshold 0.4 + fallback)."""
from __future__ import annotations

from app.execution.category_selector import CategorySelector


def test_empty_snapshot_falls_back_to_evaluator_category():
    selector = CategorySelector()
    selected, fallback = selector.select({}, evaluator_category="coding")
    assert selected == ["coding"]
    assert fallback is True


def test_no_category_above_threshold_falls_back():
    selector = CategorySelector()
    snapshot = {"coding": 0.3, "writing": 0.2, "general": 0.39}
    selected, fallback = selector.select(snapshot, evaluator_category="writing")
    assert selected == ["writing"]
    assert fallback is True


def test_single_category_above_threshold():
    selector = CategorySelector()
    snapshot = {"coding": 0.7, "writing": 0.2}
    selected, fallback = selector.select(snapshot, evaluator_category="writing")
    assert selected == ["coding"]
    assert fallback is False


def test_multiple_categories_ordered_descending():
    selector = CategorySelector()
    snapshot = {"coding": 0.5, "writing": 0.9, "general": 0.45}
    selected, fallback = selector.select(snapshot, evaluator_category="coding")
    assert selected == ["writing", "coding", "general"]
    assert fallback is False


def test_threshold_boundary_is_inclusive():
    selector = CategorySelector()
    snapshot = {"coding": 0.4}
    selected, fallback = selector.select(snapshot, evaluator_category="general")
    assert selected == ["coding"]
    assert fallback is False


def test_negative_weight_excluded():
    selector = CategorySelector()
    snapshot = {"coding": -0.1, "writing": 0.6}
    selected, fallback = selector.select(snapshot, evaluator_category="coding")
    assert selected == ["writing"]
    assert fallback is False


def test_custom_threshold():
    selector = CategorySelector(threshold=0.8)
    snapshot = {"coding": 0.7, "writing": 0.9}
    selected, fallback = selector.select(snapshot, evaluator_category="coding")
    assert selected == ["writing"]
    assert fallback is False

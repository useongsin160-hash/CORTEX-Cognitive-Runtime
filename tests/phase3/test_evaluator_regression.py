"""Phase 3 STEP 2 — keyword-vs-centroid agreement regression.

Re-runs the prompts that the Phase 2 keyword classifier handled and
checks the centroid classifier reaches the same category. Where the
two disagree we accept the centroid result IF it's semantically
defensible (per the STEP 2 brief's "disagreement policy").

This file is a guardrail against silent regressions when the
evaluator's classifier swap rolls out. It is NOT meant to lock the
keyword sieve in place — anything the centroid classifier improves
on is welcome.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.routing.centroid_store import CentroidStore
from app.routing.semantic_evaluator import SemanticEvaluator

SEED_PATH = Path(__file__).parent / "seed_queries.json"


@pytest.fixture(scope="module")
async def centroid_evaluator(tmp_path_factory) -> SemanticEvaluator:
    cache = tmp_path_factory.mktemp("eval_regression") / "centroids.npz"
    store = CentroidStore(cache_path=cache)
    await store.build_from_seeds(SEED_PATH)
    return SemanticEvaluator(centroid_store=store)


# Sourced from tests/phase2/test_routing.py::test_evaluator_classifies_each_category.
_PHASE2_PROMPTS: list[tuple[str, str]] = [
    ("I have a python bug in my class function", "coding"),
    ("design a quest for the boss level NPC", "game_design"),
    ("prove the theorem about this equation algorithm", "math_logic"),
    ("write a short essay summary translate it", "writing"),
    ("analyze the csv data with regression statistics", "data_analysis"),
    ("design the architecture for our infrastructure scalability", "system_design"),
    ("tell me a joke about a friendly platypus", "general"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,expected", _PHASE2_PROMPTS)
async def test_phase2_prompts_still_classify_correctly(
    centroid_evaluator, prompt, expected,
):
    result = await centroid_evaluator.evaluate(prompt)
    assert result.category == expected, (
        f"centroid classifier disagrees on Phase 2 prompt '{prompt}': "
        f"got {result.category}, Phase 2 keyword answer was {expected}"
    )
    assert result.classification_method == "centroid"


@pytest.mark.asyncio
async def test_short_greeting_still_difficulty_1(centroid_evaluator):
    """Difficulty heuristic is preserved — short greeting → 1."""
    result = await centroid_evaluator.evaluate("hello there friend")
    assert result.difficulty == 1


@pytest.mark.asyncio
async def test_complex_design_is_very_hard(centroid_evaluator):
    """B12 5-stage: HARD keyword route triggers VERY_HARD(4)."""
    result = await centroid_evaluator.evaluate(
        "design a scalable architecture for payments with tradeoffs"
    )
    assert result.difficulty == 4

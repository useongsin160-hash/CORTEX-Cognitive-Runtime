"""B11 S1 — category×difficulty 35-cell isolated learning store.

Verifies the additive substrate + write path:
  - target_key build/parse round-trip + difficulty<1 / malformed guards,
  - same category, different difficulty → independent cells (no merge),
  - SynapseDifficultyDryRunCalculator emits cat:diff keys, skips unset difficulty,
  - SynapseDifficultyWeightMutator writes to the 35-cell store,
  - clamp [0.1, 1.0] holds and the 1.0 ceiling is never exceeded (emergent).

The category-only synapse_weight path is untouched (covered by its own tests).
"""
from __future__ import annotations

import pytest

from app.rpe.calculators import SynapseDifficultyDryRunCalculator
from app.rpe.difficulty_store import (
    InMemorySynapseDifficultyWeightStore,
    build_cat_diff_target_key,
    parse_cat_diff_target_key,
)
from app.rpe.models import RPEContext, RPEDecision, RPEReward
from app.rpe.mutators import SynapseDifficultyWeightMutator


def _decision(category: str, difficulty: int, *, pe_pos: bool = True) -> RPEDecision:
    expected, actual = (0.3, 0.9) if pe_pos else (0.5, 0.5)
    ctx = RPEContext(
        trace_id="t", session_id="s", category=category, difficulty=difficulty
    )
    reward = RPEReward(
        source="mock", expected_reward=expected, actual_reward=actual, confidence=1.0
    )
    return RPEDecision(reward=reward, context=ctx, mode="observe_only")


# ── key build / parse ──────────────────────────────────────────────────────
def test_target_key_roundtrip():
    key = build_cat_diff_target_key("coding", 3)
    assert key == "category:coding:difficulty:3"
    assert parse_cat_diff_target_key(key) == ("coding", 3)


def test_build_rejects_unset_difficulty():
    with pytest.raises(ValueError):
        build_cat_diff_target_key("coding", 0)


@pytest.mark.parametrize(
    "bad",
    [
        "category:coding",                 # no difficulty segment
        "coding:difficulty:3",             # missing prefix
        "category:coding:difficulty:x",    # non-int difficulty
        "category:coding:difficulty:0",    # difficulty < 1
        "category::difficulty:3",          # empty category
    ],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_cat_diff_target_key(bad)


# ── store: independent cells per difficulty ────────────────────────────────
@pytest.mark.asyncio
async def test_same_category_different_difficulty_independent_cells():
    store = InMemorySynapseDifficultyWeightStore()
    await store.write_weight("s", "coding", 1, 0.2)
    await store.write_weight("s", "coding", 5, 0.9)
    assert await store.read_weight("s", "coding", 1) == 0.2
    assert await store.read_weight("s", "coding", 5) == 0.9
    # A cell never written reads as None (no merge / no backfill).
    assert await store.read_weight("s", "coding", 3) is None
    assert await store.read_weight("s", "writing", 1) is None


# ── calculator: cat:diff key + unset-difficulty skip ───────────────────────
def test_calculator_emits_cat_diff_key():
    calc = SynapseDifficultyDryRunCalculator()
    proposal = calc.compute_proposal(_decision("coding", 3), current_value=0.5)
    assert proposal is not None
    assert proposal.target == "synapse_weight"
    assert proposal.target_key == "category:coding:difficulty:3"
    # delta = pe(0.6) * conf(1.0) * max_delta(0.1) = 0.06 — difficulty NOT a factor.
    assert proposal.proposed_delta == pytest.approx(0.06)


def test_calculator_skips_unset_difficulty():
    calc = SynapseDifficultyDryRunCalculator()
    assert calc.compute_proposal(_decision("coding", 0), current_value=0.5) is None


def test_calculator_delta_independent_of_difficulty():
    calc = SynapseDifficultyDryRunCalculator()
    p1 = calc.compute_proposal(_decision("coding", 1), current_value=0.5)
    p5 = calc.compute_proposal(_decision("coding", 5), current_value=0.5)
    # Same reward → same delta regardless of difficulty (emergent invariant).
    assert p1.proposed_delta == p5.proposed_delta
    # ...but different cell addresses.
    assert p1.target_key != p5.target_key


# ── mutator: write path + accumulation + clamp ceiling ─────────────────────
@pytest.mark.asyncio
async def test_mutator_writes_and_accumulates_per_cell():
    store = InMemorySynapseDifficultyWeightStore(
        {("s", "coding", 1): 0.5, ("s", "coding", 5): 0.5}
    )
    mut = SynapseDifficultyWeightMutator(store=store)
    calc = SynapseDifficultyDryRunCalculator()

    # Apply once to coding:5 only.
    p5 = calc.compute_proposal(_decision("coding", 5), current_value=0.5)
    rec = await mut.apply_mutation(
        p5, previous_value=0.5, lock_key=f"synapse_weight:{p5.target_key}"
    )
    assert rec.new_value == pytest.approx(0.56)
    assert await store.read_weight("s", "coding", 5) == pytest.approx(0.56)
    # coding:1 untouched — independent learning.
    assert await store.read_weight("s", "coding", 1) == 0.5


@pytest.mark.asyncio
async def test_mutator_never_exceeds_ceiling():
    store = InMemorySynapseDifficultyWeightStore()
    mut = SynapseDifficultyWeightMutator(store=store)
    calc = SynapseDifficultyDryRunCalculator()
    p = calc.compute_proposal(_decision("coding", 3), current_value=0.98)
    lock_key = f"synapse_weight:{p.target_key}"

    # 0.98 + 0.06 would be 1.04 → clamped to 1.0, applied_delta only 0.02.
    rec = await mut.apply_mutation(p, previous_value=0.98, lock_key=lock_key)
    assert rec.new_value == pytest.approx(1.0)
    assert rec.applied_delta == pytest.approx(0.02)

    # At the ceiling, a further positive proposal moves nothing (no >1.0).
    rec2 = await mut.apply_mutation(p, previous_value=1.0, lock_key=lock_key)
    assert rec2.new_value == pytest.approx(1.0)
    assert rec2.applied_delta == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_mutator_rollback_restores_previous():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 3): 0.5})
    mut = SynapseDifficultyWeightMutator(store=store)
    calc = SynapseDifficultyDryRunCalculator()
    p = calc.compute_proposal(_decision("coding", 3), current_value=0.5)

    rec = await mut.apply_mutation(
        p, previous_value=0.5, lock_key=f"synapse_weight:{p.target_key}"
    )
    assert await store.read_weight("s", "coding", 3) == pytest.approx(0.56)

    rolled = await mut.rollback(rec)
    assert rolled.rollback_status == "rolled_back"
    assert await store.read_weight("s", "coding", 3) == pytest.approx(0.5)


# ── bounded LRU (no-GC fix) ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_bounded_lru_evicts_least_recently_used_cell():
    """The 35-cell store is a bounded LRU over (session, cat, diff) cells; an
    evicted cell reads back as None (truly unlearned → no routing override)."""
    store = InMemorySynapseDifficultyWeightStore(max_cells=2)
    await store.write_weight("s", "coding", 1, 0.5)
    await store.write_weight("s", "coding", 2, 0.6)
    await store.read_weight("s", "coding", 1)        # touch cell1 → cell2 is LRU
    await store.write_weight("s", "coding", 3, 0.7)  # over cap → evict cell2
    assert await store.read_weight("s", "coding", 2) is None
    assert await store.read_weight("s", "coding", 1) == 0.5
    assert await store.read_weight("s", "coding", 3) == 0.7

"""B11 S2 — difficulty learner (write activation) + gate (read/overlay).

Verifies:
  - learner writes the current (cat,diff) cell (seed 0.3 → +delta), gated by
    difficulty_learning_enabled, skips unset difficulty, accumulates independently
    per difficulty;
  - gate overlays the learned current cell onto the snapshot, no-ops on an
    unlearned cell, and never merges across difficulties (only the current cell).
"""
from __future__ import annotations

import pytest

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import SpinalLogger
from app.rpe.calculators import SynapseDifficultyDryRunCalculator
from app.rpe.difficulty_gate import SynapseDifficultyGate
from app.rpe.difficulty_learner import RPEDifficultyLearner
from app.rpe.difficulty_store import InMemorySynapseDifficultyWeightStore
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import ActiveMutationConfig, RPEContext
from app.rpe.mutators import SynapseDifficultyWeightMutator
from app.rpe.service import RPEMutationService
from app.rpe.sources import MockRewardSource


def _learner(
    store: InMemorySynapseDifficultyWeightStore,
    *,
    learning: bool = True,
    trace: str = "trace",
    reward: tuple[float, float] = (0.3, 0.9),  # PE 0.6, confidence 1.0
) -> RPEDifficultyLearner:
    mutator = SynapseDifficultyWeightMutator(store=store)
    service = RPEMutationService(
        mutator=mutator,
        logger=SpinalLogger(),
        config=ActiveMutationConfig(
            active_enabled=True, difficulty_learning_enabled=learning
        ),
    )
    dopamine = DopamineRPE(
        sources=[MockRewardSource(reward_map={trace: reward})], logger=SpinalLogger()
    )
    return RPEDifficultyLearner(
        dopamine_rpe=dopamine,
        calculator=SynapseDifficultyDryRunCalculator(),
        service=service,
        logger=SpinalLogger(),
    )


def _ctx(trace: str, category: str, difficulty: int) -> RPEContext:
    return RPEContext(
        trace_id=trace, session_id="sess", category=category, difficulty=difficulty
    )


# ── learner: write path ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_learner_writes_current_cell_from_seed():
    store = InMemorySynapseDifficultyWeightStore()
    learner = _learner(store, trace="s2-write")
    records = await learner.learn(_ctx("s2-write", "coding", 4))
    assert len(records) == 1
    # seed 0.3 + delta(0.6 * 1.0 * 0.1 = 0.06) = 0.36
    assert await store.read_weight("sess", "coding", 4) == pytest.approx(0.36)
    # other cells stay unlearned (None) — no seeding pollution elsewhere.
    assert await store.read_weight("sess", "coding", 1) is None


@pytest.mark.asyncio
async def test_learner_noop_when_disabled():
    store = InMemorySynapseDifficultyWeightStore()
    learner = _learner(store, learning=False, trace="s2-off")
    records = await learner.learn(_ctx("s2-off", "coding", 4))
    assert records == []
    assert store.snapshot() == {}


@pytest.mark.asyncio
async def test_learner_skips_unset_difficulty():
    store = InMemorySynapseDifficultyWeightStore()
    learner = _learner(store, trace="s2-unset")
    records = await learner.learn(_ctx("s2-unset", "coding", 0))
    assert records == []
    assert store.snapshot() == {}


@pytest.mark.asyncio
async def test_learner_accumulates_independently_per_difficulty():
    store = InMemorySynapseDifficultyWeightStore()
    # Same category, different difficulty, distinct traces → independent cells.
    await _learner(store, trace="d1").learn(_ctx("d1", "coding", 1))
    await _learner(store, trace="d5").learn(_ctx("d5", "coding", 5))
    assert await store.read_weight("sess", "coding", 1) == pytest.approx(0.36)
    assert await store.read_weight("sess", "coding", 5) == pytest.approx(0.36)
    # Different cells — proven distinct keys, not a merged value.
    assert ("sess", "coding", 1) in store.snapshot()
    assert ("sess", "coding", 5) in store.snapshot()
    assert ("sess", "coding", 3) not in store.snapshot()


@pytest.mark.asyncio
async def test_learner_no_write_on_zero_signal():
    # MockRewardSource default (0.5, 0.5) → PE 0 → zero_delta → blocked, no write.
    store = InMemorySynapseDifficultyWeightStore()
    learner = _learner(store, trace="s2-zero", reward=(0.5, 0.5))
    records = await learner.learn(_ctx("s2-zero", "coding", 4))
    assert records == []
    assert store.snapshot() == {}


# ── gate: read / overlay ───────────────────────────────────────────────────
def _tc(category: str, difficulty: Difficulty, snapshot: dict[str, float]) -> TaskContext:
    return TaskContext(
        trace_id="t", category=category, difficulty=difficulty, synapse_snapshot=snapshot
    )


@pytest.mark.asyncio
async def test_gate_overlays_learned_current_cell():
    store = InMemorySynapseDifficultyWeightStore({("sess", "coding", 4): 0.8})
    gate = SynapseDifficultyGate(store=store, logger=SpinalLogger())
    tc = _tc("coding", Difficulty.VERY_HARD, {"coding": 0.3, "writing": 0.5})
    await gate.overlay(tc, "sess")
    assert tc.synapse_snapshot["coding"] == pytest.approx(0.8)   # overlaid
    assert tc.synapse_snapshot["writing"] == pytest.approx(0.5)  # untouched


@pytest.mark.asyncio
async def test_gate_noop_on_unlearned_cell():
    store = InMemorySynapseDifficultyWeightStore()  # empty
    gate = SynapseDifficultyGate(store=store, logger=SpinalLogger())
    tc = _tc("coding", Difficulty.VERY_HARD, {"coding": 0.7})
    await gate.overlay(tc, "sess")
    assert tc.synapse_snapshot["coding"] == pytest.approx(0.7)  # SynapseObserver value stands


@pytest.mark.asyncio
async def test_gate_does_not_merge_across_difficulty():
    # Learned at difficulty 1, but the current request is difficulty 4 → the
    # diff-4 cell is None → no overlay (no cross-difficulty bleed).
    store = InMemorySynapseDifficultyWeightStore({("sess", "coding", 1): 0.9})
    gate = SynapseDifficultyGate(store=store, logger=SpinalLogger())
    tc = _tc("coding", Difficulty.VERY_HARD, {"coding": 0.4})
    await gate.overlay(tc, "sess")
    assert tc.synapse_snapshot["coding"] == pytest.approx(0.4)  # diff-1 value did NOT leak


@pytest.mark.asyncio
async def test_gate_disabled_is_noop():
    store = InMemorySynapseDifficultyWeightStore({("sess", "coding", 4): 0.8})
    gate = SynapseDifficultyGate(store=store, logger=SpinalLogger(), enabled=False)
    tc = _tc("coding", Difficulty.VERY_HARD, {"coding": 0.3})
    await gate.overlay(tc, "sess")
    assert tc.synapse_snapshot["coding"] == pytest.approx(0.3)

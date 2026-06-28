"""B3b — global EMA preset + presetted difficulty store tests.

Covers: EMA roll-up math, persist/load roundtrip across instances (restart sim),
clamp on load, the session→preset→None read fallback, write_weight being
session-only (decay never reaches the preset), the difficulty service's
learning-only roll-up, B11 compat (mutator seeds from the preset), and the
frozen no-roll-up path.
"""
from __future__ import annotations

import aiosqlite
import pytest

from app.core.logging import SpinalLogger
from app.rpe.calculators import SynapseDifficultyDryRunCalculator
from app.rpe.difficulty_learner import RPEDifficultyLearner
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import ActiveMutationConfig, RPEContext
from app.rpe.mutators import SynapseDifficultyWeightMutator
from app.rpe.preset_store import DifficultyPresetStore, PresettedDifficultyStore
from app.rpe.service import RPEMutationService
from app.rpe.sources import MockRewardSource


def _db_url(tmp_path, name: str = "preset.db") -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


# ── EMA math + roundtrip ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ema_first_write_is_value(tmp_path):
    store = DifficultyPresetStore(_db_url(tmp_path), alpha=0.3)
    await store.update_ema("coding", 2, 0.8)
    assert store.get_preset("coding", 2) == pytest.approx(0.8)  # no prev → value


@pytest.mark.asyncio
async def test_ema_blends_with_alpha(tmp_path):
    store = DifficultyPresetStore(_db_url(tmp_path), alpha=0.3)
    await store.update_ema("coding", 2, 0.8)
    await store.update_ema("coding", 2, 0.4)
    # 0.3*0.4 + 0.7*0.8 = 0.68
    assert store.get_preset("coding", 2) == pytest.approx(0.68)


@pytest.mark.asyncio
async def test_persist_load_roundtrip_across_instances(tmp_path):
    url = _db_url(tmp_path)
    store = DifficultyPresetStore(url, alpha=0.3)
    await store.update_ema("coding", 2, 0.8)
    await store.update_ema("writing", 5, 0.55)

    reopened = DifficultyPresetStore(url)
    assert reopened.get_preset("coding", 2) is None  # cache empty until load_all
    await reopened.load_all()
    assert reopened.get_preset("coding", 2) == pytest.approx(0.8)
    assert reopened.get_preset("writing", 5) == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_update_ema_clamps(tmp_path):
    store = DifficultyPresetStore(_db_url(tmp_path))
    await store.update_ema("coding", 2, 1.5)   # over max
    assert store.get_preset("coding", 2) == pytest.approx(1.0)
    await store.update_ema("coding", 3, 0.0)   # under min
    assert store.get_preset("coding", 3) == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_load_clamps_out_of_bounds_row(tmp_path):
    """A hand-edited / legacy out-of-bounds row is clamped on load."""
    url = _db_url(tmp_path)
    store = DifficultyPresetStore(url)
    await store.update_ema("coding", 2, 0.5)  # creates the table
    # write a raw out-of-bounds row directly.
    async with aiosqlite.connect(tmp_path / "preset.db") as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO rpe_difficulty_weights "
            "(category, difficulty, weight, updated_at) VALUES ('coding', 2, 9.9, 'x')"
        )
        await conn.commit()
    await store.load_all()
    assert store.get_preset("coding", 2) == pytest.approx(1.0)  # clamped


def test_invalid_alpha_rejected(tmp_path):
    with pytest.raises(ValueError, match="alpha"):
        DifficultyPresetStore(_db_url(tmp_path), alpha=0.0)


# ── PresettedDifficultyStore read fallback + session-only write ──────────────
@pytest.mark.asyncio
async def test_read_fallback_session_then_preset_then_none(tmp_path):
    preset = DifficultyPresetStore(_db_url(tmp_path))
    await preset.update_ema("coding", 3, 0.7)
    store = PresettedDifficultyStore(preset=preset)

    # no session value → preset fallback.
    assert await store.read_weight("s1", "coding", 3) == pytest.approx(0.7)
    # no session + no preset → None.
    assert await store.read_weight("s1", "writing", 1) is None
    # session value takes priority over preset.
    await store.write_weight("s1", "coding", 3, 0.5)
    assert await store.read_weight("s1", "coding", 3) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_write_weight_does_not_touch_preset(tmp_path):
    """decay + mutator both go through write_weight → must NOT roll up."""
    preset = DifficultyPresetStore(_db_url(tmp_path))
    await preset.update_ema("coding", 2, 0.6)
    store = PresettedDifficultyStore(preset=preset)
    await store.write_weight("s1", "coding", 2, 0.2)  # e.g. a decay write
    assert preset.get_preset("coding", 2) == pytest.approx(0.6)  # unchanged


# ── difficulty service learning-only roll-up ─────────────────────────────────
def _difficulty_service(preset, store, *, learning=True):
    return RPEMutationService(
        mutator=SynapseDifficultyWeightMutator(store=store),
        logger=SpinalLogger(),
        config=ActiveMutationConfig(active_enabled=True, difficulty_learning_enabled=learning),
        preset_store=preset,
    )


async def _learn(service, store, *, trace, session="s1", category="coding", difficulty=2):
    dopamine = DopamineRPE(
        sources=[MockRewardSource(reward_map={trace: (0.3, 0.9)})], logger=SpinalLogger()
    )
    learner = RPEDifficultyLearner(
        dopamine_rpe=dopamine,
        calculator=SynapseDifficultyDryRunCalculator(),
        service=service,
        logger=SpinalLogger(),
    )
    ctx = RPEContext(trace_id=trace, session_id=session, category=category, difficulty=difficulty)
    return await learner.learn(ctx)


@pytest.mark.asyncio
async def test_learning_mutation_rolls_up_to_preset(tmp_path):
    preset = DifficultyPresetStore(_db_url(tmp_path), alpha=0.3)
    store = PresettedDifficultyStore(preset=preset)
    service = _difficulty_service(preset, store)

    recs = await _learn(service, store, trace="t1")
    assert len(recs) == 1
    # seed 0.3 + PE 0.6*conf 1.0*0.1 = 0.36 → first roll-up = value.
    assert preset.get_preset("coding", 2) == pytest.approx(0.36)


@pytest.mark.asyncio
async def test_b11_mutator_seeds_from_preset(tmp_path):
    """A learned global preset becomes the mutator's previous_value for an
    unlearned-in-session cell (not the 0.3 seed) — learning continues on top."""
    preset = DifficultyPresetStore(_db_url(tmp_path), alpha=0.3)
    await preset.update_ema("coding", 2, 0.5)  # preset present
    store = PresettedDifficultyStore(preset=preset)
    service = _difficulty_service(preset, store)

    recs = await _learn(service, store, trace="t1")
    assert recs[0].previous_value == pytest.approx(0.5)  # seeded from preset, not 0.3
    assert recs[0].new_value == pytest.approx(0.56)


@pytest.mark.asyncio
async def test_frozen_no_rollup(tmp_path):
    preset = DifficultyPresetStore(_db_url(tmp_path))
    store = PresettedDifficultyStore(preset=preset)
    service = _difficulty_service(preset, store, learning=False)  # B13 freeze
    recs = await _learn(service, store, trace="t1")
    assert recs == []
    assert preset.snapshot() == {}  # nothing rolled up


@pytest.mark.asyncio
async def test_rollup_failure_is_fail_open(tmp_path):
    class _RaisingPreset:
        def get_preset(self, c, d):
            return None
        async def update_ema(self, c, d, v):
            raise RuntimeError("db down")
    store = PresettedDifficultyStore(preset=DifficultyPresetStore(_db_url(tmp_path)))
    service = RPEMutationService(
        mutator=SynapseDifficultyWeightMutator(store=store),
        logger=SpinalLogger(),
        config=ActiveMutationConfig(active_enabled=True, difficulty_learning_enabled=True),
        preset_store=_RaisingPreset(),
    )
    recs = await _learn(service, store, trace="t1")
    assert len(recs) == 1  # apply succeeded despite roll-up raising


# ── bounded LRU (no-GC fix) — evicted session cell degrades to the preset ────
@pytest.mark.asyncio
async def test_presetted_bounded_lru_evicts_to_preset(tmp_path):
    preset = DifficultyPresetStore(_db_url(tmp_path), alpha=0.3)
    await preset.update_ema("coding", 1, 0.8)  # global preset for (coding, 1)
    store = PresettedDifficultyStore(preset=preset, max_cells=1)
    await store.write_weight("s", "coding", 1, 0.5)        # session cell
    await store.write_weight("s", "writing", 2, 0.6)       # over cap=1 → evict (s,coding,1)
    # the evicted session cell falls back to the LEARNED preset (never worse than it).
    assert await store.read_weight("s", "coding", 1) == pytest.approx(0.8)
    # the most-recent cell survives.
    assert await store.read_weight("s", "writing", 2) == pytest.approx(0.6)

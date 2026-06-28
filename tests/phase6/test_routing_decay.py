"""B11 S5 — step-based lazy routing decay (weight forgetting → floor release).

Counterpart of the S4 ratchet: idle cells forget (weight decays), and below the
release threshold the routing floor is lowered one band toward the difficulty's
B12-native baseline (high difficulty stays protected). Verifies lazy realize,
consecutive-use exemption, weight floor clamp, baseline exemption, and the
decay → demote restoration that closes the S4/S5 asymmetry.
"""
from __future__ import annotations

import pytest

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import SpinalLogger
from app.routing.routing_decay import (
    DECAY_RATE,
    RoutingDecay,
    _baseline_band,
)
from app.routing.routing_ratchet import RoutingRatchet
from app.routing.skip_router import RouteDecision
from app.rpe.difficulty_store import InMemorySynapseDifficultyWeightStore


def _setup() -> tuple[InMemorySynapseDifficultyWeightStore, RoutingRatchet, RoutingDecay]:
    store = InMemorySynapseDifficultyWeightStore()
    ratchet = RoutingRatchet(logger=SpinalLogger())
    decay = RoutingDecay(store=store, ratchet=ratchet, logger=SpinalLogger())
    return store, ratchet, decay


def _tc(category: str, difficulty: Difficulty) -> TaskContext:
    return TaskContext(trace_id="t", category=category, difficulty=difficulty)


async def _idle_then_revisit(decay, store, category, difficulty, idle_requests, other="writing"):
    """First-use the cell, advance `idle_requests` other-cell steps, revisit it."""
    await decay.step(_tc(category, difficulty), "s")  # first use → last_used
    for _ in range(idle_requests):
        await decay.step(_tc(other, Difficulty.MEDIUM), "s")
    await decay.step(_tc(category, difficulty), "s")  # revisit → realize idle decay


# ── lazy realize / exemptions ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_idle_decay_realized_on_revisit():
    store, _, decay = _setup()
    store.set("s", "coding", 2, 0.80)
    await _idle_then_revisit(decay, store, "coding", Difficulty.MEDIUM, idle_requests=1)
    # idle = 1 request → one step of decay.
    assert await store.read_weight("s", "coding", 2) == pytest.approx(0.80 - DECAY_RATE)


@pytest.mark.asyncio
async def test_consecutive_use_no_decay():
    store, _, decay = _setup()
    store.set("s", "coding", 2, 0.80)
    await decay.step(_tc("coding", Difficulty.MEDIUM), "s")  # step 1
    await decay.step(_tc("coding", Difficulty.MEDIUM), "s")  # step 2, idle 0
    assert await store.read_weight("s", "coding", 2) == pytest.approx(0.80)


@pytest.mark.asyncio
async def test_unlearned_cell_not_decayed():
    store, _, decay = _setup()  # store empty
    await _idle_then_revisit(decay, store, "coding", Difficulty.MEDIUM, idle_requests=3)
    assert store.snapshot() == {}  # nothing written — no value to decay


@pytest.mark.asyncio
async def test_weight_floor_clamped_at_min():
    store, _, decay = _setup()
    store.set("s", "coding", 2, 0.12)
    await _idle_then_revisit(decay, store, "coding", Difficulty.MEDIUM, idle_requests=50)
    assert await store.read_weight("s", "coding", 2) == pytest.approx(0.1)  # emergent min


# ── floor release + baseline exemption ─────────────────────────────────────
@pytest.mark.asyncio
async def test_decay_below_threshold_releases_floor_one_band():
    store, ratchet, decay = _setup()
    await ratchet.apply(RouteDecision(path="full_pipeline", skip_layers=[], reason="x"),
                        _tc("coding", Difficulty.MEDIUM), "s")  # floor full_pipeline
    store.set("s", "coding", 2, 0.42)
    await _idle_then_revisit(decay, store, "coding", Difficulty.MEDIUM, idle_requests=5)
    # 0.42 - 5*0.01 = 0.37 < 0.4 → release one band toward baseline (standard).
    assert ratchet._floors["s"][("coding", 2)] == "standard"


@pytest.mark.asyncio
async def test_b12_native_baseline_floor_exempt():
    store, ratchet, decay = _setup()
    # difficulty 5 (DEEP_THINKING) baseline = full_pipeline.
    await ratchet.apply(RouteDecision(path="full_pipeline", skip_layers=[], reason="x"),
                        _tc("coding", Difficulty.DEEP_THINKING), "s")
    store.set("s", "coding", 5, 0.30)
    await _idle_then_revisit(decay, store, "coding", Difficulty.DEEP_THINKING, idle_requests=2)
    # below threshold, but baseline=full_pipeline → floor stays full (protected).
    assert ratchet._floors["s"][("coding", 5)] == "full_pipeline"


def test_baseline_band_mapping():
    assert _baseline_band(1) == "lightweight"
    assert _baseline_band(2) == "standard"
    assert _baseline_band(3) == "standard"
    assert _baseline_band(4) == "full_pipeline"
    assert _baseline_band(5) == "full_pipeline"


# ── decay restores demote (S4 ratchet ↔ S5 decay asymmetry closes) ─────────
@pytest.mark.asyncio
async def test_decay_restores_demote_after_forgetting():
    store, ratchet, decay = _setup()
    # promoted cell: floor full_pipeline (S4 locked — demote was blocked).
    await ratchet.apply(RouteDecision(path="full_pipeline", skip_layers=[], reason="x"),
                        _tc("coding", Difficulty.MEDIUM), "s")
    blocked = await ratchet.apply(RouteDecision(path="lightweight", skip_layers=[], reason="x"),
                                  _tc("coding", Difficulty.MEDIUM), "s")
    assert blocked.path == "full_pipeline"  # S4: demote blocked

    # forget it: weight decays below threshold over idle → floor released one band.
    store.set("s", "coding", 2, 0.45)
    await _idle_then_revisit(decay, store, "coding", Difficulty.MEDIUM, idle_requests=10)

    # floor lowered full_pipeline → standard → a demote toward standard now passes.
    out = await ratchet.apply(RouteDecision(path="lightweight", skip_layers=[], reason="x"),
                              _tc("coding", Difficulty.MEDIUM), "s")
    assert out.path == "standard"  # demotion partially restored by forgetting


# ── bounded LRU ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_bounded_lru_evicts_oldest_session():
    store = InMemorySynapseDifficultyWeightStore()
    ratchet = RoutingRatchet(logger=SpinalLogger())
    decay = RoutingDecay(store=store, ratchet=ratchet, logger=SpinalLogger(), max_sessions=2)
    await decay.step(_tc("coding", Difficulty.MEDIUM), "s1")
    await decay.step(_tc("coding", Difficulty.MEDIUM), "s2")
    await decay.step(_tc("coding", Difficulty.MEDIUM), "s3")  # > 2 → evict s1
    assert len(decay._sessions) == 2
    assert "s1" not in decay._sessions

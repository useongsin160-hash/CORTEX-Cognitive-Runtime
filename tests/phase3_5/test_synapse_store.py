"""Phase 3.5 STEP 1 — SynapseStore session isolation + snapshot."""
from __future__ import annotations

import json

import pytest

from app.synapse.categories import INITIAL_WEIGHT, SYNAPSE_CATEGORIES
from app.synapse.store import SynapseState, SynapseStore


@pytest.mark.asyncio
async def test_new_session_has_seven_categories_at_initial_weight():
    store = SynapseStore()
    state = await store.get_state("session-a")
    assert set(state.weights.keys()) == set(SYNAPSE_CATEGORIES)
    assert all(w == INITIAL_WEIGHT for w in state.weights.values())


@pytest.mark.asyncio
async def test_repeated_get_state_returns_same_instance():
    store = SynapseStore()
    first = await store.get_state("session-a")
    second = await store.get_state("session-a")
    assert first is second


@pytest.mark.asyncio
async def test_distinct_sessions_are_independent():
    store = SynapseStore()
    a = await store.get_state("session-a")
    b = await store.get_state("session-b")
    assert a is not b
    a.last_observed_category = "coding"
    assert b.last_observed_category is None


@pytest.mark.asyncio
async def test_snapshot_is_plain_json_safe_dict():
    store = SynapseStore()
    snap = await store.snapshot("session-a")
    assert isinstance(snap, dict)
    assert set(snap.keys()) == set(SYNAPSE_CATEGORIES)
    assert all(isinstance(v, float) for v in snap.values())
    # Must round-trip through JSON without error.
    assert json.loads(json.dumps(snap)) == snap


@pytest.mark.asyncio
async def test_snapshot_is_a_copy_not_a_live_reference():
    store = SynapseStore()
    snap = await store.snapshot("session-a")
    snap["coding"] = 0.99
    state = await store.get_state("session-a")
    assert state.weights["coding"] == INITIAL_WEIGHT


@pytest.mark.asyncio
async def test_reset_state_returns_all_weights_to_initial():
    store = SynapseStore()
    state = await store.get_state("session-a")
    state.weights["coding"] = 0.9
    await store.reset_state("session-a")
    reset = await store.get_state("session-a")
    assert all(w == INITIAL_WEIGHT for w in reset.weights.values())


def test_synapse_state_drops_unknown_category_keys():
    """Unknown keys are dropped (무시); missing ones backfilled to 0.3."""
    state = SynapseState(weights={"coding": 0.5, "not_a_category": 0.7})
    assert "not_a_category" not in state.weights
    assert set(state.weights.keys()) == set(SYNAPSE_CATEGORIES)
    assert state.weights["coding"] == 0.5
    assert state.weights["general"] == INITIAL_WEIGHT


@pytest.mark.asyncio
async def test_bounded_lru_evicts_least_recently_used_session():
    """No-GC fix: the session map is a bounded LRU (8GB host)."""
    store = SynapseStore(max_sessions=2)
    await store.get_state("s1")
    await store.get_state("s2")
    await store.get_state("s1")          # touch s1 → s2 becomes LRU
    await store.get_state("s3")          # over cap → evict s2 (the LRU)
    assert set(store._states.keys()) == {"s1", "s3"}

"""Phase 3.5 STEP 1 — FlushPolicy (cosine 0.35 threshold).

Flush fires iff cosine(prev, new) < 0.35 (strictly below). Test vectors
use integer components so the cosine is a clean rational well clear of
the 0.35 knife-edge — float32 rounding never flips the verdict.
"""
from __future__ import annotations

import pytest

from app.synapse.categories import INITIAL_WEIGHT, SYNAPSE_CATEGORIES
from app.synapse.policies import FlushPolicy
from app.synapse.store import SynapseState

_BASE = [1.0, 0.0, 0.0]
# cosine([1,4,0], base) = 1/sqrt(17) ≈ 0.2425  → below 0.35 → flush
_SIM_BELOW = [1.0, 4.0, 0.0]
# cosine([5,12,0], base) = 5/13 ≈ 0.3846       → at/above 0.35 → no flush
_SIM_AT_OR_ABOVE = [5.0, 12.0, 0.0]
# cosine([4,3,0], base) = 4/5 = 0.80            → well above → no flush
_SIM_HIGH = [4.0, 3.0, 0.0]


@pytest.fixture
def policy() -> FlushPolicy:
    return FlushPolicy()


@pytest.mark.asyncio
async def test_no_flush_when_no_prior_embedding(policy):
    state = SynapseState()  # last_observed_embedding is None
    assert await policy.should_flush(state, _BASE) is False


@pytest.mark.asyncio
async def test_flush_when_similarity_below_threshold(policy):
    state = SynapseState(last_observed_embedding=_BASE)
    assert await policy.should_flush(state, _SIM_BELOW) is True


@pytest.mark.asyncio
async def test_no_flush_when_similarity_above_threshold(policy):
    state = SynapseState(last_observed_embedding=_BASE)
    assert await policy.should_flush(state, _SIM_HIGH) is False


@pytest.mark.asyncio
async def test_no_flush_at_or_above_threshold_band(policy):
    """cosine 5/13 ≈ 0.3846 ≥ 0.35 → no flush (flush is strictly below)."""
    state = SynapseState(last_observed_embedding=_BASE)
    assert await policy.should_flush(state, _SIM_AT_OR_ABOVE) is False


@pytest.mark.asyncio
async def test_no_flush_when_new_embedding_empty(policy):
    state = SynapseState(last_observed_embedding=_BASE)
    assert await policy.should_flush(state, []) is False


@pytest.mark.asyncio
async def test_apply_flush_resets_all_weights_to_initial(policy):
    state = SynapseState()
    for cat in SYNAPSE_CATEGORIES:
        state.weights[cat] = 0.9
    await policy.apply_flush(state)
    assert all(w == INITIAL_WEIGHT for w in state.weights.values())
    assert set(state.weights.keys()) == set(SYNAPSE_CATEGORIES)


@pytest.mark.asyncio
async def test_apply_flush_increments_flush_count(policy):
    state = SynapseState()
    assert state.flush_count == 0
    await policy.apply_flush(state)
    assert state.flush_count == 1
    await policy.apply_flush(state)
    assert state.flush_count == 2


@pytest.mark.asyncio
async def test_apply_flush_updates_last_flush_at(policy):
    state = SynapseState()
    assert state.last_flush_at is None
    await policy.apply_flush(state)
    assert isinstance(state.last_flush_at, float)
    assert state.last_flush_at > 0

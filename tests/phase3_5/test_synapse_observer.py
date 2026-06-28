"""Phase 3.5 STEP 1 — SynapseObserver (Observe step)."""
from __future__ import annotations

import pytest

from app.core.logging import get_spinal_logger
from app.synapse.categories import INITIAL_WEIGHT
from app.synapse.observer import SynapseObserver
from app.synapse.policies import FlushPolicy
from app.synapse.store import SynapseStore

_BASE = [1.0, 0.0, 0.0]
_FAR = [1.0, 4.0, 0.0]   # cosine vs _BASE ≈ 0.2425 → triggers Flush


def _observer() -> tuple[SynapseObserver, SynapseStore]:
    store = SynapseStore()
    return SynapseObserver(store=store, flush_policy=FlushPolicy()), store


@pytest.mark.asyncio
async def test_observe_records_last_observed_fields():
    observer, store = _observer()
    await observer.observe("s1", "coding", _BASE, 0.42)
    state = await store.get_state("s1")
    assert state.last_observed_category == "coding"
    assert state.last_observed_similarity == 0.42
    assert state.last_observed_embedding == _BASE


@pytest.mark.asyncio
async def test_observe_does_not_mutate_weights():
    observer, store = _observer()
    await observer.observe("s1", "coding", _BASE, 0.42)
    state = await store.get_state("s1")
    # Observe must never change weights — that is Phase 6 RPE territory.
    assert all(w == INITIAL_WEIGHT for w in state.weights.values())


@pytest.mark.asyncio
async def test_observe_triggers_flush_on_context_switch():
    observer, store = _observer()
    # First observation — no prior embedding, no flush.
    await observer.observe("s1", "coding", _BASE, 0.42)
    state = await store.get_state("s1")
    state.weights["coding"] = 0.9  # simulate drift to be cleared by Flush

    # Second observation is semantically far → Flush.
    await observer.observe("s1", "writing", _FAR, 0.40)
    state = await store.get_state("s1")
    assert state.flush_count == 1
    assert all(w == INITIAL_WEIGHT for w in state.weights.values())
    # last_observed_* refreshed to the new (post-flush) query.
    assert state.last_observed_category == "writing"
    assert state.last_observed_embedding == _FAR


@pytest.mark.asyncio
async def test_observe_logs_synapse_observed_event():
    observer, _ = _observer()
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    await observer.observe("s1", "coding", _BASE, 0.42, trace_id=trace_id)
    types = [e.event_type for e in logger.get_trace(trace_id)]
    assert "synapse.observed" in types


@pytest.mark.asyncio
async def test_observe_logs_synapse_flushed_event_on_flush():
    observer, _ = _observer()
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    await observer.observe("s1", "coding", _BASE, 0.42, trace_id=trace_id)
    await observer.observe("s1", "writing", _FAR, 0.40, trace_id=trace_id)
    types = [e.event_type for e in logger.get_trace(trace_id)]
    assert "synapse.flushed" in types
    assert types.count("synapse.observed") == 2

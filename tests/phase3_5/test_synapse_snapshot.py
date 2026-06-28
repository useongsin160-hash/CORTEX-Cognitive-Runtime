"""Phase 3.5 STEP 1 — SynapseSnapshotter."""
from __future__ import annotations

import json

import pytest

from app.synapse.categories import SYNAPSE_CATEGORIES
from app.synapse.snapshot import SynapseSnapshotter
from app.synapse.store import SynapseStore


@pytest.mark.asyncio
async def test_snapshot_has_seven_category_keys():
    store = SynapseStore()
    snapshotter = SynapseSnapshotter(store=store)
    snap = await snapshotter.take_snapshot("s1")
    assert set(snap.keys()) == set(SYNAPSE_CATEGORIES)


@pytest.mark.asyncio
async def test_snapshot_values_are_floats():
    store = SynapseStore()
    snapshotter = SynapseSnapshotter(store=store)
    snap = await snapshotter.take_snapshot("s1")
    assert all(isinstance(v, float) for v in snap.values())


@pytest.mark.asyncio
async def test_snapshot_is_json_serializable():
    store = SynapseStore()
    snapshotter = SynapseSnapshotter(store=store)
    snap = await snapshotter.take_snapshot("s1")
    dumped = json.dumps(snap)
    assert json.loads(dumped) == snap


@pytest.mark.asyncio
async def test_snapshots_are_session_independent():
    store = SynapseStore()
    snapshotter = SynapseSnapshotter(store=store)
    state_a = await store.get_state("s1")
    state_a.weights["coding"] = 0.77
    snap_a = await snapshotter.take_snapshot("s1")
    snap_b = await snapshotter.take_snapshot("s2")
    assert snap_a["coding"] == 0.77
    assert snap_b["coding"] == 0.3

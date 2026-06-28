"""Phase 6 STEP 5.1 — BasalGanglia read-only / no-mutation tests.

Verifies that BasalGanglia never modifies any of its inputs:
- Synapse weight mapping unchanged
- IFOM override mapping unchanged
- PFC snapshot object unchanged
- LC snapshot object unchanged
- No mutation methods called
"""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.basal_ganglia.advisor import (
    BasalGangliaAdvisor,
    build_action_selection_context_from_snapshots,
)


def _snapshot(d: dict) -> dict:
    """Deep copy of dict for before/after equality comparison."""
    return copy.deepcopy(d)


def test_synapse_weights_input_unchanged():
    source = {"coding": 0.6, "writing": 0.4, "math_logic": 0.5}
    before = _snapshot(source)
    build_action_selection_context_from_snapshots(
        trace_id="tr-rd", session_id="s", category="coding", difficulty=1,
        synapse_weights=source,
    )
    assert source == before


def test_ifom_overrides_input_unchanged():
    source = {"active:coding": 3600.0, "paused:writing": 1800.0}
    before = _snapshot(source)
    build_action_selection_context_from_snapshots(
        trace_id="tr-rd", session_id="s", category="coding", difficulty=1,
        ifom_ttl_overrides=source,
    )
    assert source == before


def test_pfc_snapshot_object_unchanged():
    pfc_snap = SimpleNamespace(
        pfc_active=True, cue_type="completion", confidence=0.75,
        intent_category="coding",
    )
    before = dict(vars(pfc_snap))
    build_action_selection_context_from_snapshots(
        trace_id="tr", session_id="s", category="coding", difficulty=1,
        pfc_snapshot=pfc_snap,
    )
    assert dict(vars(pfc_snap)) == before


def test_lc_snapshot_object_unchanged():
    lc_snap = SimpleNamespace(ne_level=0.7, intent_label="caution")
    before = dict(vars(lc_snap))
    build_action_selection_context_from_snapshots(
        trace_id="tr", session_id="s", category="coding", difficulty=1,
        lc_snapshot=lc_snap,
    )
    assert dict(vars(lc_snap)) == before


def test_advisor_evaluate_does_not_mutate_synapse_weights():
    source = {"coding": 0.55}
    before = _snapshot(source)
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-eval", session_id="s", category="coding", difficulty=1,
        synapse_weights=source,
    )
    advisor = BasalGangliaAdvisor()
    asyncio.run(advisor.evaluate(ctx))
    assert source == before


def test_advisor_evaluate_does_not_mutate_pfc_snapshot():
    pfc_snap = SimpleNamespace(
        pfc_active=True, cue_type="completion", confidence=0.75,
        intent_category=None,
    )
    before = dict(vars(pfc_snap))
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id="s", category="coding", difficulty=1,
        pfc_snapshot=pfc_snap,
    )
    advisor = BasalGangliaAdvisor()
    asyncio.run(advisor.evaluate(ctx))
    assert dict(vars(pfc_snap)) == before


def test_advisor_evaluate_does_not_mutate_lc_snapshot():
    lc_snap = SimpleNamespace(ne_level=0.6, intent_label="caution")
    before = dict(vars(lc_snap))
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id="s", category="coding", difficulty=1,
        lc_snapshot=lc_snap,
    )
    advisor = BasalGangliaAdvisor()
    asyncio.run(advisor.evaluate(ctx))
    assert dict(vars(lc_snap)) == before


class _Tracker:
    """Snapshot-like object that tracks whether any mutation method is called."""

    def __init__(self):
        self.pfc_active = True
        self.confidence = 0.5
        self.cue_type = "continuation"
        self.ne_level = 0.4
        self.intent_label = "go"
        self.mutation_count = 0

    def update(self, *args, **kwargs):  # would-be mutation method
        self.mutation_count += 1

    def write(self, *args, **kwargs):
        self.mutation_count += 1

    def set(self, *args, **kwargs):
        self.mutation_count += 1


def test_no_mutation_method_called_on_snapshots():
    tracker = _Tracker()
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id="s", category="coding", difficulty=1,
        pfc_snapshot=tracker, lc_snapshot=tracker,
    )
    advisor = BasalGangliaAdvisor()
    asyncio.run(advisor.evaluate(ctx))
    assert tracker.mutation_count == 0


def test_context_field_identity_preserved_across_evaluate():
    """Multiple calls with same context produce decisions sharing the same ctx ref."""
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-id", session_id="s", category="coding", difficulty=1,
        synapse_weights={"coding": 0.6},
    )
    advisor = BasalGangliaAdvisor()
    d1 = asyncio.run(advisor.evaluate(ctx))
    d2 = asyncio.run(advisor.evaluate(ctx))
    assert d1.context is ctx
    assert d2.context is ctx
    # And both decisions remain applied=False
    assert d1.applied is False
    assert d2.applied is False

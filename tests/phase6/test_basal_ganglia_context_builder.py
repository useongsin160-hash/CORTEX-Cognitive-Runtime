"""Phase 6 STEP 5.1 — build_action_selection_context_from_snapshots tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.basal_ganglia.advisor import build_action_selection_context_from_snapshots


# ---------------------------------------------------------------------------
# Mapping → tuple-of-pairs snapshot
# ---------------------------------------------------------------------------


def test_synapse_weights_mapping_becomes_tuple_of_pairs():
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-1",
        session_id="s",
        category="coding",
        difficulty=2,
        synapse_weights={"coding": 0.6, "writing": 0.4},
    )
    assert isinstance(ctx.synapse_weights, tuple)
    pairs = dict(ctx.synapse_weights)
    assert pairs == {"coding": 0.6, "writing": 0.4}


def test_synapse_weights_source_mutation_does_not_affect_context():
    source = {"coding": 0.6}
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-1", session_id="s", category="coding", difficulty=1,
        synapse_weights=source,
    )
    source["coding"] = 0.99
    source["new_key"] = 0.1
    # Context must remain unchanged
    pairs = dict(ctx.synapse_weights)
    assert pairs == {"coding": 0.6}


def test_ifom_overrides_mapping_becomes_tuple_of_pairs():
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-1", session_id="s", category="coding", difficulty=1,
        ifom_ttl_overrides={"active:coding": 3600.0},
    )
    assert isinstance(ctx.ifom_ttl_overrides, tuple)
    assert ctx.ifom_ttl_overrides == (("active:coding", 3600.0),)


def test_ifom_overrides_source_mutation_does_not_affect_context():
    source = {"active:coding": 3600.0}
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-1", session_id="s", category="coding", difficulty=1,
        ifom_ttl_overrides=source,
    )
    source["active:coding"] = 9999.0
    source["paused:coding"] = 1800.0
    pairs = dict(ctx.ifom_ttl_overrides)
    assert pairs == {"active:coding": 3600.0}


def test_none_mappings_default_to_empty_tuples():
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr-1", session_id="s", category="coding", difficulty=0,
    )
    assert ctx.synapse_weights == ()
    assert ctx.ifom_ttl_overrides == ()


# ---------------------------------------------------------------------------
# PFC snapshot getattr extraction
# ---------------------------------------------------------------------------


def test_pfc_snapshot_extraction():
    pfc_snap = SimpleNamespace(
        pfc_active=True, cue_type="completion", confidence=0.75,
        intent_category="coding",
    )
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category="coding", difficulty=1,
        pfc_snapshot=pfc_snap,
    )
    assert ctx.pfc_active is True
    assert ctx.pfc_cue_type == "completion"
    assert ctx.pfc_confidence == 0.75
    assert ctx.pfc_intent_category == "coding"


def test_pfc_snapshot_partial_fields():
    pfc_snap = SimpleNamespace(confidence=0.55)
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        pfc_snapshot=pfc_snap,
    )
    # No pfc_active attr → derived from cue_type/confidence presence
    assert ctx.pfc_active is True  # since confidence is present
    assert ctx.pfc_cue_type is None
    assert ctx.pfc_confidence == 0.55


def test_pfc_snapshot_none():
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        pfc_snapshot=None,
    )
    assert ctx.pfc_active is False
    assert ctx.pfc_cue_type is None
    assert ctx.pfc_confidence is None
    assert ctx.pfc_intent_category is None


def test_pfc_snapshot_bool_field_ignored_for_confidence():
    """If 'confidence' is True/False, it must NOT be coerced to 1.0/0.0."""
    pfc_snap = SimpleNamespace(confidence=True)  # weird input
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        pfc_snapshot=pfc_snap,
    )
    assert ctx.pfc_confidence is None  # bool refused


# ---------------------------------------------------------------------------
# LC snapshot getattr extraction
# ---------------------------------------------------------------------------


def test_lc_snapshot_extraction():
    lc_snap = SimpleNamespace(ne_level=0.42, intent_label="caution")
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        lc_snapshot=lc_snap,
    )
    assert ctx.lc_ne_level == 0.42
    assert ctx.lc_intent_label == "caution"


def test_lc_snapshot_none():
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        lc_snapshot=None,
    )
    assert ctx.lc_ne_level is None
    assert ctx.lc_intent_label is None


def test_lc_snapshot_partial():
    lc_snap = SimpleNamespace(ne_level=0.7)
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        lc_snapshot=lc_snap,
    )
    assert ctx.lc_ne_level == 0.7
    assert ctx.lc_intent_label is None


# ---------------------------------------------------------------------------
# No direct type import required
# ---------------------------------------------------------------------------


def test_builder_uses_duck_typing_only():
    """Random object with relevant attrs should work — no concrete class needed."""

    class Anon:
        def __init__(self):
            self.confidence = 0.5
            self.cue_type = "completion"
            self.ne_level = 0.3

    obj = Anon()
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        pfc_snapshot=obj, lc_snapshot=obj,
    )
    assert ctx.pfc_confidence == 0.5
    assert ctx.pfc_cue_type == "completion"
    assert ctx.lc_ne_level == 0.3


# ---------------------------------------------------------------------------
# Difficulty coercion
# ---------------------------------------------------------------------------


def test_difficulty_coerced_to_int():
    ctx = build_action_selection_context_from_snapshots(
        trace_id="tr", session_id=None, category=None, difficulty=3.0,  # type: ignore[arg-type]
    )
    assert ctx.difficulty == 3
    assert isinstance(ctx.difficulty, int)

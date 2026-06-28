"""Phase 6 STEP 5.1 — BasalGanglia frozen dataclass model tests."""
from __future__ import annotations

import dataclasses

import pytest

from app.basal_ganglia.models import (
    ActionCandidate,
    ActionSelectionContext,
    ActionSelectionDecision,
    ActionSelectionPolicyConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(**overrides) -> ActionSelectionContext:
    defaults = dict(
        trace_id="tr-1",
        session_id="s1",
        category="coding",
        difficulty=2,
        pfc_active=True,
        pfc_cue_type=None,
        pfc_confidence=0.7,
        pfc_intent_category=None,
        lc_ne_level=0.3,
        lc_intent_label=None,
    )
    defaults.update(overrides)
    return ActionSelectionContext(**defaults)


def _cand(**overrides) -> ActionCandidate:
    defaults = dict(
        candidate_id="c1",
        candidate_type="swarm_full",
        target_category="coding",
    )
    defaults.update(overrides)
    return ActionCandidate(**defaults)


# ---------------------------------------------------------------------------
# Frozen invariants
# ---------------------------------------------------------------------------


def test_action_candidate_is_frozen():
    cand = _cand()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cand.candidate_id = "other"  # type: ignore[misc]


def test_action_selection_context_is_frozen():
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.trace_id = "other"  # type: ignore[misc]


def test_action_selection_decision_is_frozen():
    ctx = _ctx()
    cand = _cand()
    decision = ActionSelectionDecision(
        context=ctx,
        candidates=(cand,),
        selected=cand,
        confidence=0.5,
        reason="r",
        applied=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.confidence = 1.0  # type: ignore[misc]


def test_policy_config_is_frozen():
    cfg = ActionSelectionPolicyConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.ne_demand_factor = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ActionCandidate validation
# ---------------------------------------------------------------------------


def test_candidate_id_empty_raises():
    with pytest.raises(ValueError, match="candidate_id"):
        _cand(candidate_id="")


def test_candidate_id_non_str_raises():
    with pytest.raises(ValueError, match="candidate_id"):
        _cand(candidate_id=123)  # type: ignore[arg-type]


def test_candidate_type_unknown_raises():
    with pytest.raises(ValueError, match="candidate_type"):
        _cand(candidate_type="bogus")  # type: ignore[arg-type]


def test_candidate_synapse_weight_out_of_range_raises():
    with pytest.raises(ValueError, match="synapse_weight"):
        _cand(synapse_weight=1.5)


def test_candidate_synapse_weight_negative_raises():
    with pytest.raises(ValueError, match="synapse_weight"):
        _cand(synapse_weight=-0.1)


def test_candidate_pfc_confidence_out_of_range_raises():
    with pytest.raises(ValueError, match="pfc_confidence"):
        _cand(pfc_confidence=2.0)


def test_candidate_lc_ne_level_out_of_range_raises():
    with pytest.raises(ValueError, match="lc_ne_level"):
        _cand(lc_ne_level=-1.0)


def test_candidate_rpe_count_negative_raises():
    with pytest.raises(ValueError, match="rpe_recent_positive_count"):
        _cand(rpe_recent_positive_count=-1)


def test_candidate_metadata_non_scalar_raises():
    with pytest.raises(TypeError, match="JSON scalar"):
        _cand(metadata=(("k", [1, 2]),))  # type: ignore[arg-type]


def test_candidate_metadata_empty_key_raises():
    with pytest.raises(ValueError, match="metadata key"):
        _cand(metadata=(("", "v"),))


# ---------------------------------------------------------------------------
# ActionSelectionContext validation
# ---------------------------------------------------------------------------


def test_context_trace_id_empty_raises():
    with pytest.raises(ValueError, match="trace_id"):
        _ctx(trace_id="")


def test_context_negative_difficulty_raises():
    with pytest.raises(ValueError, match="difficulty"):
        _ctx(difficulty=-1)


def test_context_synapse_weight_out_of_range_raises():
    with pytest.raises(ValueError, match="synapse_weights"):
        _ctx(synapse_weights=(("coding", 1.5),))


def test_context_duplicate_synapse_category_raises():
    with pytest.raises(ValueError, match="duplicate synapse_weights"):
        _ctx(synapse_weights=(("coding", 0.5), ("coding", 0.7)))


def test_context_duplicate_ifom_ttl_override_key_raises():
    with pytest.raises(ValueError, match="duplicate ifom_ttl_overrides"):
        _ctx(
            ifom_ttl_overrides=(
                ("active:coding", 3600.0),
                ("active:coding", 7200.0),
            )
        )


def test_context_ifom_ttl_override_zero_raises():
    with pytest.raises(ValueError, match="ifom_ttl_overrides"):
        _ctx(ifom_ttl_overrides=(("active:coding", 0.0),))


def test_context_ifom_ttl_override_negative_raises():
    with pytest.raises(ValueError, match="ifom_ttl_overrides"):
        _ctx(ifom_ttl_overrides=(("active:coding", -1.0),))


def test_context_pfc_confidence_out_of_range_raises():
    with pytest.raises(ValueError, match="pfc_confidence"):
        _ctx(pfc_confidence=1.5)


def test_context_lc_ne_level_out_of_range_raises():
    with pytest.raises(ValueError, match="lc_ne_level"):
        _ctx(lc_ne_level=-0.1)


def test_context_rpe_count_negative_raises():
    with pytest.raises(ValueError, match="rpe_recent_negative_count"):
        _ctx(rpe_recent_negative_count=-1)


def test_context_metadata_non_scalar_raises():
    with pytest.raises(TypeError, match="JSON scalar"):
        _ctx(metadata=(("k", {"v": 1}),))  # type: ignore[arg-type]


def test_context_tuple_of_pairs_is_immutable_to_source_change():
    """Mutating the source mapping after building tuple-of-pairs must not affect ctx."""
    pairs = (("coding", 0.6),)
    ctx = _ctx(synapse_weights=pairs)
    # No way to mutate a tuple — verify identity preservation.
    assert ctx.synapse_weights == pairs


# ---------------------------------------------------------------------------
# ActionSelectionDecision validation
# ---------------------------------------------------------------------------


def test_decision_applied_true_raises():
    ctx = _ctx()
    cand = _cand()
    with pytest.raises(ValueError, match="applied must be False"):
        ActionSelectionDecision(
            context=ctx, candidates=(cand,), selected=cand,
            confidence=0.5, reason="r", applied=True,
        )


def test_decision_confidence_out_of_range_raises():
    ctx = _ctx()
    cand = _cand()
    with pytest.raises(ValueError, match="confidence"):
        ActionSelectionDecision(
            context=ctx, candidates=(cand,), selected=cand,
            confidence=1.5, reason="r",
        )


def test_decision_confidence_negative_raises():
    ctx = _ctx()
    cand = _cand()
    with pytest.raises(ValueError, match="confidence"):
        ActionSelectionDecision(
            context=ctx, candidates=(cand,), selected=cand,
            confidence=-0.1, reason="r",
        )


def test_decision_empty_reason_raises():
    ctx = _ctx()
    cand = _cand()
    with pytest.raises(ValueError, match="reason"):
        ActionSelectionDecision(
            context=ctx, candidates=(cand,), selected=cand,
            confidence=0.5, reason="",
        )


def test_decision_selected_not_in_candidates_raises():
    ctx = _ctx()
    cand_a = _cand(candidate_id="a")
    cand_b = _cand(candidate_id="b")
    with pytest.raises(ValueError, match="member of candidates"):
        ActionSelectionDecision(
            context=ctx, candidates=(cand_a,), selected=cand_b,
            confidence=0.5, reason="r",
        )


def test_decision_no_selected_allowed():
    ctx = _ctx()
    decision = ActionSelectionDecision(
        context=ctx,
        candidates=(),
        selected=None,
        confidence=0.0,
        reason="no_candidates",
        applied=False,
    )
    assert decision.selected is None


def test_decision_applied_default_false():
    ctx = _ctx()
    cand = _cand()
    decision = ActionSelectionDecision(
        context=ctx, candidates=(cand,), selected=cand,
        confidence=0.5, reason="r",
    )
    assert decision.applied is False


# ---------------------------------------------------------------------------
# PolicyConfig validation
# ---------------------------------------------------------------------------


def test_policy_config_defaults():
    cfg = ActionSelectionPolicyConfig()
    assert cfg.ne_demand_factor == 0.20
    assert cfg.rpe_demand_factor == 0.15
    assert cfg.synapse_demand_factor == 0.15
    assert cfg.pfc_demand_factor == 0.10


def test_policy_config_negative_factor_raises():
    with pytest.raises(ValueError, match="ne_demand_factor"):
        ActionSelectionPolicyConfig(ne_demand_factor=-0.1)


def test_policy_config_negative_pfc_factor_raises():
    with pytest.raises(ValueError, match="pfc_demand_factor"):
        ActionSelectionPolicyConfig(pfc_demand_factor=-0.5)


def test_policy_config_custom_factors():
    """Modulation factors are freely tunable (deviation magnitudes)."""
    cfg = ActionSelectionPolicyConfig(
        ne_demand_factor=0.30,
        rpe_demand_factor=0.20,
        synapse_demand_factor=0.20,
        pfc_demand_factor=0.15,
    )
    assert cfg.ne_demand_factor == 0.30

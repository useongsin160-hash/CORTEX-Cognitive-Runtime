"""Phase 6 STEP 5.1 (redesigned) — ActionSelectionPolicy compute-demand tests.

The policy now scores each candidate by how closely its compute level L matches the
context's compute demand D, where D is a difficulty-band anchor modulated by the
real signals (NE, RPE balance, synapse familiarity, PFC confidence). These tests
lock the demand formula, the routing-aligned baseline, the modulation direction,
and the deterministic tie-breaker.
"""
from __future__ import annotations

import pytest

from app.basal_ganglia.models import (
    ActionCandidate,
    ActionSelectionContext,
    ActionSelectionPolicyConfig,
)
from app.basal_ganglia.policies import ActionSelectionPolicy

# Compute levels (mirror policies._TYPE_COMPUTE_LEVEL).
_FULL = 1.0
_MINIMAL = 2.0 / 3.0
_TIER = 1.0 / 3.0
_FALLBACK = 0.0


def _ctx(**overrides) -> ActionSelectionContext:
    defaults = dict(
        trace_id="tr",
        session_id=None,
        category="coding",
        difficulty=2,
        pfc_active=False,
        pfc_cue_type=None,
        pfc_confidence=None,
        pfc_intent_category=None,
        lc_ne_level=None,
        lc_intent_label=None,
    )
    defaults.update(overrides)
    return ActionSelectionContext(**defaults)


def _cand(candidate_type="swarm_full", candidate_id=None, **overrides) -> ActionCandidate:
    defaults = dict(
        candidate_id=candidate_id or candidate_type,
        candidate_type=candidate_type,
        target_category="coding",
    )
    defaults.update(overrides)
    return ActionCandidate(**defaults)


def _all_default_candidates() -> tuple[ActionCandidate, ...]:
    return (
        _cand("swarm_full"),
        _cand("swarm_minimal"),
        _cand("tier_1_5_augment"),
        _cand("fallback"),
    )


# ---------------------------------------------------------------------------
# Empty candidates
# ---------------------------------------------------------------------------


def test_empty_candidates_no_selected():
    policy = ActionSelectionPolicy()
    selected, confidence, reason = policy.select(_ctx(), ())
    assert selected is None
    assert confidence == 0.0
    assert reason == "no_candidates"


# ---------------------------------------------------------------------------
# Demand formula (anchor + signed modulation)
# ---------------------------------------------------------------------------


def test_demand_difficulty_anchor_no_signals():
    """No modulators present → D equals the B12 difficulty band anchor."""
    policy = ActionSelectionPolicy()
    assert policy._demand(_ctx(difficulty=1))[0] == pytest.approx(_TIER)
    assert policy._demand(_ctx(difficulty=2))[0] == pytest.approx(_MINIMAL)
    assert policy._demand(_ctx(difficulty=3))[0] == pytest.approx(_MINIMAL)
    assert policy._demand(_ctx(difficulty=4))[0] == pytest.approx(_FULL)
    assert policy._demand(_ctx(difficulty=5))[0] == pytest.approx(_FULL)
    assert policy._demand(_ctx(difficulty=2))[1] == "difficulty"


def test_demand_ne_escalates():
    policy = ActionSelectionPolicy()
    d, primary = policy._demand(_ctx(difficulty=2, lc_ne_level=1.0))
    assert d == pytest.approx(_MINIMAL + 0.20)
    assert primary == "ne"


def test_demand_rpe_failures_escalate_successes_de_escalate():
    policy = ActionSelectionPolicy()
    fail = policy._demand(
        _ctx(difficulty=2, rpe_recent_positive_count=0, rpe_recent_negative_count=5)
    )
    success = policy._demand(
        _ctx(difficulty=2, rpe_recent_positive_count=5, rpe_recent_negative_count=0)
    )
    assert fail[0] == pytest.approx(_MINIMAL + 0.15)  # neg_frac=1 → +factor
    assert success[0] == pytest.approx(_MINIMAL - 0.15)  # neg_frac=0 → -factor
    assert fail[1] == "rpe" and success[1] == "rpe"


def test_demand_synapse_familiar_de_escalates_unfamiliar_escalates():
    policy = ActionSelectionPolicy()
    familiar = policy._demand(_ctx(difficulty=2, synapse_weights=(("coding", 1.0),)))
    unfamiliar = policy._demand(_ctx(difficulty=2, synapse_weights=(("coding", 0.0),)))
    assert familiar[0] == pytest.approx(_MINIMAL - 0.15)
    assert unfamiliar[0] == pytest.approx(_MINIMAL + 0.15)


def test_demand_pfc_confident_de_escalates_uncertain_escalates():
    policy = ActionSelectionPolicy()
    confident = policy._demand(_ctx(difficulty=2, pfc_confidence=1.0))
    uncertain = policy._demand(_ctx(difficulty=2, pfc_confidence=0.0))
    assert confident[0] == pytest.approx(_MINIMAL - 0.10)
    assert uncertain[0] == pytest.approx(_MINIMAL + 0.10)


def test_demand_neutral_signals_leave_baseline():
    """ne 0, balanced RPE, synapse/pfc at 0.5 → zero net deviation."""
    policy = ActionSelectionPolicy()
    d, _ = policy._demand(
        _ctx(
            difficulty=2,
            lc_ne_level=0.0,
            pfc_confidence=0.5,
            synapse_weights=(("coding", 0.5),),
            rpe_recent_positive_count=3,
            rpe_recent_negative_count=3,
        )
    )
    assert d == pytest.approx(_MINIMAL)


def test_demand_clamped_to_one():
    policy = ActionSelectionPolicy()
    d, _ = policy._demand(
        _ctx(
            difficulty=5,
            lc_ne_level=1.0,
            pfc_confidence=0.0,
            synapse_weights=(("coding", 0.0),),
            rpe_recent_positive_count=0,
            rpe_recent_negative_count=5,
        )
    )
    assert d == 1.0


def test_demand_clamped_to_zero():
    policy = ActionSelectionPolicy()
    d, _ = policy._demand(
        _ctx(
            difficulty=1,
            pfc_confidence=1.0,
            synapse_weights=(("coding", 1.0),),
            rpe_recent_positive_count=5,
            rpe_recent_negative_count=0,
        )
    )
    # anchor 1/3 − 0.15(synapse) − 0.10(pfc) − 0.15(rpe) = −0.067 → clamped to 0.
    assert d == 0.0


def test_demand_missing_signal_omitted_not_fabricated():
    """A None signal contributes 0 — identical to its neutral value."""
    policy = ActionSelectionPolicy()
    none_pfc = policy._demand(_ctx(difficulty=2, pfc_confidence=None))[0]
    neutral_pfc = policy._demand(_ctx(difficulty=2, pfc_confidence=0.5))[0]
    assert none_pfc == pytest.approx(neutral_pfc) == pytest.approx(_MINIMAL)


# ---------------------------------------------------------------------------
# Selection — routing-aligned baseline + direction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "difficulty,expected",
    [
        (1, "tier_1_5_augment"),  # lightweight band
        (2, "swarm_minimal"),     # standard band
        (3, "swarm_minimal"),
        (4, "swarm_full"),        # full_pipeline band
        (5, "swarm_full"),
    ],
)
def test_neutral_selection_matches_routing_baseline(difficulty, expected):
    policy = ActionSelectionPolicy()
    selected, _, _ = policy.select(_ctx(difficulty=difficulty), _all_default_candidates())
    assert selected.candidate_type == expected


def test_strong_escalation_promotes_low_difficulty():
    """A low-difficulty but unfamiliar + failing + uncertain query escalates."""
    policy = ActionSelectionPolicy()
    ctx = _ctx(
        difficulty=2,
        lc_ne_level=1.0,
        pfc_confidence=0.0,
        synapse_weights=(("coding", 0.0),),
        rpe_recent_positive_count=0,
        rpe_recent_negative_count=5,
    )
    selected, _, _ = policy.select(ctx, _all_default_candidates())
    assert selected.candidate_type == "swarm_full"


def test_strong_de_escalation_lightens_low_difficulty():
    """A familiar + confident + successful low-difficulty query goes lighter."""
    policy = ActionSelectionPolicy()
    ctx = _ctx(
        difficulty=2,
        lc_ne_level=0.0,
        pfc_confidence=1.0,
        synapse_weights=(("coding", 1.0),),
        rpe_recent_positive_count=5,
        rpe_recent_negative_count=0,
    )
    selected, _, _ = policy.select(ctx, _all_default_candidates())
    assert selected.candidate_type in ("tier_1_5_augment", "fallback")


def test_high_difficulty_stays_full_under_mild_signals():
    """difficulty 5 with production-shape NE stays at swarm_full."""
    policy = ActionSelectionPolicy()
    ctx = _ctx(difficulty=5, lc_ne_level=1.0, pfc_confidence=0.6,
               synapse_weights=(("coding", 0.5),))
    selected, _, _ = policy.select(ctx, _all_default_candidates())
    assert selected.candidate_type == "swarm_full"


# ---------------------------------------------------------------------------
# Deterministic tie-breaker
# ---------------------------------------------------------------------------


def test_tie_broken_by_id_lex_when_same_type():
    """Two candidates of the same type share L → exact score tie → id lex asc."""
    policy = ActionSelectionPolicy()
    c_b = _cand("swarm_full", candidate_id="b")
    c_a = _cand("swarm_full", candidate_id="a")
    selected, _, _ = policy.select(_ctx(difficulty=4), (c_b, c_a))
    assert selected.candidate_id == "a"


def test_exact_level_match_wins_outright():
    """At a baseline anchor the exact-level candidate beats all others (no tie)."""
    policy = ActionSelectionPolicy()
    selected, conf, _ = policy.select(_ctx(difficulty=4), _all_default_candidates())
    assert selected.candidate_type == "swarm_full"  # level 1.0 == demand 1.0
    assert conf == pytest.approx(1.0 * 0.6 + (1.0 - _MINIMAL) * 0.4)


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_confidence_single_candidate_uses_score():
    policy = ActionSelectionPolicy()
    # difficulty 2 → demand 2/3; swarm_minimal level 2/3 → score 1.0.
    _, conf, _ = policy.select(_ctx(difficulty=2), (_cand("swarm_minimal"),))
    assert conf == pytest.approx(1.0)


def test_confidence_uses_margin_when_multiple():
    policy = ActionSelectionPolicy()
    # difficulty 2 → demand 2/3. minimal score=1.0, full score=1-1/3=2/3.
    selected, conf, _ = policy.select(
        _ctx(difficulty=2), (_cand("swarm_full"), _cand("swarm_minimal"))
    )
    assert selected.candidate_type == "swarm_minimal"
    top, margin = 1.0, 1.0 - _MINIMAL
    assert conf == pytest.approx(top * 0.6 + margin * 0.4)


def test_confidence_clamped_to_one():
    policy = ActionSelectionPolicy()
    _, conf, _ = policy.select(_ctx(difficulty=4), (_cand("swarm_full"),))
    assert conf <= 1.0


# ---------------------------------------------------------------------------
# Reason string
# ---------------------------------------------------------------------------


def test_reason_reports_level_demand_match():
    policy = ActionSelectionPolicy()
    _, _, reason = policy.select(_ctx(difficulty=4), _all_default_candidates())
    assert "type=swarm_full" in reason
    assert "level=1.000" in reason
    assert "demand=" in reason and "match=" in reason


# ---------------------------------------------------------------------------
# Config injection
# ---------------------------------------------------------------------------


def test_zero_factors_degenerate_to_pure_difficulty_routing():
    """All modulation factors 0 → D is the difficulty anchor regardless of signals."""
    cfg = ActionSelectionPolicyConfig(
        ne_demand_factor=0.0, rpe_demand_factor=0.0,
        synapse_demand_factor=0.0, pfc_demand_factor=0.0,
    )
    policy = ActionSelectionPolicy(config=cfg)
    ctx = _ctx(
        difficulty=2, lc_ne_level=1.0, pfc_confidence=0.0,
        synapse_weights=(("coding", 0.0),),
        rpe_recent_positive_count=0, rpe_recent_negative_count=9,
    )
    d, _ = policy._demand(ctx)
    assert d == pytest.approx(_MINIMAL)
    selected, _, _ = policy.select(ctx, _all_default_candidates())
    assert selected.candidate_type == "swarm_minimal"

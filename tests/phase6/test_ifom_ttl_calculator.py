"""Phase 6 STEP 4 — IFOMTTLDryRunCalculator unit tests."""
from __future__ import annotations

import pytest

from app.rpe.calculators import IFOMTTLDryRunCalculator
from app.rpe.models import (
    DryRunConfig,
    RPEContext,
    RPEDecision,
    RPEProposal,
    RPEReward,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_decision(
    trace_id: str = "tr-1",
    session_id: str = "sess-1",
    category: str | None = "coding",
    expected_reward: float = 0.5,
    actual_reward: float = 0.8,
    confidence: float = 0.7,
    source: str = "heuristic",
) -> RPEDecision:
    reward = RPEReward(
        source=source,  # type: ignore[arg-type]
        expected_reward=expected_reward,
        actual_reward=actual_reward,
        confidence=confidence,
    )
    context = RPEContext(
        trace_id=trace_id,
        session_id=session_id,
        category=category,
    )
    return RPEDecision(reward=reward, context=context)


def _make_config(**kwargs) -> DryRunConfig:
    defaults: dict = dict(
        enabled_targets=("ifom_ttl",),
        ifom_ttl_max_delta=300.0,
        ifom_ttl_min_seconds=60.0,
        ifom_ttl_max_seconds=86400.0,
    )
    defaults.update(kwargs)
    return DryRunConfig(**defaults)


# ---------------------------------------------------------------------------
# Basic computation
# ---------------------------------------------------------------------------


def test_compute_proposal_returns_proposal():
    cfg = _make_config()
    calc = IFOMTTLDryRunCalculator(config=cfg)
    decision = _make_decision()
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is not None
    assert isinstance(proposal, RPEProposal)


def test_compute_proposal_target_is_ifom_ttl():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    proposal = calc.compute_proposal(_make_decision(), ttl_type="active")
    assert proposal is not None
    assert proposal.target == "ifom_ttl"


def test_compute_proposal_target_key_format():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    proposal = calc.compute_proposal(_make_decision(category="coding"), ttl_type="active")
    assert proposal is not None
    assert proposal.target_key == "active:coding"


def test_compute_proposal_paused_ttl_type():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    proposal = calc.compute_proposal(_make_decision(category="writing"), ttl_type="paused")
    assert proposal is not None
    assert proposal.target_key == "paused:writing"


def test_compute_proposal_all_ttl_types():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    decision = _make_decision()
    for ttl_type in ("active", "paused", "completed", "low_priority"):
        p = calc.compute_proposal(decision, ttl_type=ttl_type)  # type: ignore[arg-type]
        assert p is not None
        assert p.target_key == f"{ttl_type}:coding"


def test_compute_proposal_delta_formula():
    """delta = clamp(pe * conf * max_delta, -max, +max)."""
    cfg = _make_config(ifom_ttl_max_delta=300.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    # pe = 0.8 - 0.5 = 0.3, conf = 0.7
    # raw = 0.3 * 0.7 * 300 = 63.0
    decision = _make_decision(expected_reward=0.5, actual_reward=0.8, confidence=0.7)
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is not None
    assert abs(proposal.proposed_delta - 63.0) < 1e-6


def test_compute_proposal_delta_clamped_to_max():
    """Large PE × conf saturates at max_delta."""
    cfg = _make_config(ifom_ttl_max_delta=60.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    decision = _make_decision(expected_reward=0.0, actual_reward=1.0, confidence=1.0)
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is not None
    assert abs(proposal.proposed_delta - 60.0) < 1e-9


def test_compute_proposal_negative_pe_negative_delta():
    cfg = _make_config(ifom_ttl_max_delta=300.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    # pe = 0.2 - 0.8 = -0.6, conf = 0.9
    decision = _make_decision(expected_reward=0.8, actual_reward=0.2, confidence=0.9)
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is not None
    assert proposal.proposed_delta < 0


def test_compute_proposal_max_delta_used():
    cfg = _make_config(ifom_ttl_max_delta=300.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    proposal = calc.compute_proposal(_make_decision(), ttl_type="active")
    assert proposal is not None
    assert proposal.max_delta == 300.0


def test_compute_proposal_confidence_matches():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    decision = _make_decision(confidence=0.75)
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is not None
    assert abs(proposal.confidence - 0.75) < 1e-9


def test_compute_proposal_rollback_id_valid_uuid4():
    import uuid
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    proposal = calc.compute_proposal(_make_decision(), ttl_type="active")
    assert proposal is not None
    u = uuid.UUID(proposal.rollback_id, version=4)
    assert str(u) == proposal.rollback_id


# ---------------------------------------------------------------------------
# proposed_value with current_value
# ---------------------------------------------------------------------------


def test_proposed_value_none_when_no_current():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    proposal = calc.compute_proposal(_make_decision(), ttl_type="active")
    assert proposal is not None
    assert proposal.current_value is None
    assert proposal.proposed_value is None


def test_proposed_value_computed_when_current_given():
    cfg = _make_config(ifom_ttl_max_delta=300.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    # pe = 0.3, conf = 0.7, delta = 63.0
    decision = _make_decision(expected_reward=0.5, actual_reward=0.8, confidence=0.7)
    proposal = calc.compute_proposal(decision, ttl_type="active", current_value=3600.0)
    assert proposal is not None
    assert proposal.current_value == 3600.0
    # proposed_value = clamp(3600 + 63, 60, 86400)
    assert abs(proposal.proposed_value - 3663.0) < 1e-6


def test_proposed_value_clamped_to_max():
    cfg = _make_config(ifom_ttl_max_delta=300.0, ifom_ttl_max_seconds=3000.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    decision = _make_decision(expected_reward=0.0, actual_reward=1.0, confidence=1.0)
    proposal = calc.compute_proposal(decision, ttl_type="active", current_value=2900.0)
    assert proposal is not None
    assert proposal.proposed_value == 3000.0


def test_proposed_value_clamped_to_min():
    cfg = _make_config(ifom_ttl_max_delta=300.0, ifom_ttl_min_seconds=120.0)
    calc = IFOMTTLDryRunCalculator(config=cfg)
    decision = _make_decision(expected_reward=1.0, actual_reward=0.0, confidence=1.0)
    proposal = calc.compute_proposal(decision, ttl_type="active", current_value=200.0)
    assert proposal is not None
    assert proposal.proposed_value == 120.0


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def test_skip_when_ifom_ttl_not_enabled():
    cfg = DryRunConfig(enabled_targets=("synapse_weight",))
    calc = IFOMTTLDryRunCalculator(config=cfg)
    proposal = calc.compute_proposal(_make_decision(), ttl_type="active")
    assert proposal is None


def test_skip_when_no_category():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    decision = _make_decision(category=None)
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is None


def test_skip_when_empty_category():
    calc = IFOMTTLDryRunCalculator(config=_make_config())
    decision = _make_decision(category="")
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is None


def test_skip_when_category_not_allowed():
    cfg = _make_config(allowed_categories=("coding",))
    calc = IFOMTTLDryRunCalculator(config=cfg)
    decision = _make_decision(category="unknown_cat")
    proposal = calc.compute_proposal(decision, ttl_type="active")
    assert proposal is None


# ---------------------------------------------------------------------------
# Custom rollback_id factory
# ---------------------------------------------------------------------------


def test_custom_rollback_id_factory():
    import uuid
    factory_calls = []
    fixed_id = str(uuid.uuid4())

    def factory():
        factory_calls.append(1)
        return fixed_id

    calc = IFOMTTLDryRunCalculator(
        config=_make_config(), rollback_id_factory=factory
    )
    proposal = calc.compute_proposal(_make_decision(), ttl_type="active")
    assert proposal is not None
    assert len(factory_calls) == 1
    assert proposal.rollback_id == fixed_id

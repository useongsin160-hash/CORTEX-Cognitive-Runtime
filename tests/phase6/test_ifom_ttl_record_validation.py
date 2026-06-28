"""Phase 6 STEP 4 — RPEMutationRecord target-aware validation tests.

Tests that RPEMutationRecord validates lock_key prefix based on proposal.target:
- synapse_weight → lock_key must start with "synapse_weight:"
- ifom_ttl → lock_key must start with "ifom_ttl:"
- unknown target → raises ValueError
"""
from __future__ import annotations

import uuid

import pytest

from app.rpe.models import (
    RPEContext,
    RPEDecision,
    RPEMutationRecord,
    RPEProposal,
    RPEReward,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    trace_id: str = "tr-1",
    session_id: str = "sess-1",
    category: str = "coding",
) -> RPEDecision:
    reward = RPEReward(
        source="heuristic",
        expected_reward=0.3,
        actual_reward=0.8,
        confidence=0.7,
    )
    ctx = RPEContext(trace_id=trace_id, session_id=session_id, category=category)
    return RPEDecision(reward=reward, context=ctx)


def _make_synapse_proposal(decision: RPEDecision | None = None) -> RPEProposal:
    if decision is None:
        decision = _make_decision()
    return RPEProposal(
        decision=decision,
        target="synapse_weight",
        target_key="category:coding",
        current_value=None,
        proposed_delta=0.05,
        proposed_value=None,
        max_delta=0.1,
        rollback_id=str(uuid.uuid4()),
        confidence=decision.reward.confidence,
    )


def _make_ifom_proposal(
    decision: RPEDecision | None = None,
    ttl_type: str = "active",
) -> RPEProposal:
    if decision is None:
        decision = _make_decision()
    return RPEProposal(
        decision=decision,
        target="ifom_ttl",
        target_key=f"{ttl_type}:coding",
        current_value=None,
        proposed_delta=120.0,
        proposed_value=None,
        max_delta=300.0,
        rollback_id=str(uuid.uuid4()),
        confidence=decision.reward.confidence,
    )


def _make_record(proposal: RPEProposal, lock_key: str, **kwargs) -> RPEMutationRecord:
    """Build an RPEMutationRecord with appropriate bounds for target."""
    target = proposal.target
    if target == "synapse_weight":
        weight_min, weight_max = kwargs.pop("weight_min", 0.1), kwargs.pop("weight_max", 1.0)
        prev = kwargs.pop("previous_value", 0.5)
        new = kwargs.pop("new_value", 0.55)
        delta = kwargs.pop("applied_delta", 0.05)
    else:
        weight_min, weight_max = kwargs.pop("weight_min", 60.0), kwargs.pop("weight_max", 86400.0)
        prev = kwargs.pop("previous_value", 3600.0)
        new = kwargs.pop("new_value", 3720.0)
        delta = kwargs.pop("applied_delta", 120.0)

    return RPEMutationRecord(
        proposal=proposal,
        previous_value=prev,
        applied_delta=delta,
        new_value=new,
        applied_at=1000.0,
        rollback_id=proposal.rollback_id,
        lock_key=lock_key,
        weight_min=weight_min,
        weight_max=weight_max,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# synapse_weight records
# ---------------------------------------------------------------------------


def test_synapse_weight_valid_lock_key():
    proposal = _make_synapse_proposal()
    record = _make_record(proposal, lock_key="synapse_weight:category:coding")
    assert record.lock_key == "synapse_weight:category:coding"
    assert record.proposal.target == "synapse_weight"


def test_synapse_weight_wrong_lock_key_raises():
    proposal = _make_synapse_proposal()
    with pytest.raises(ValueError, match="synapse_weight"):
        _make_record(proposal, lock_key="ifom_ttl:active:coding")


def test_synapse_weight_ifom_lock_key_raises():
    proposal = _make_synapse_proposal()
    with pytest.raises(ValueError, match="'synapse_weight:'"):
        _make_record(proposal, lock_key="ifom_ttl:active:coding")


# ---------------------------------------------------------------------------
# ifom_ttl records
# ---------------------------------------------------------------------------


def test_ifom_ttl_valid_lock_key():
    proposal = _make_ifom_proposal()
    record = _make_record(proposal, lock_key="ifom_ttl:active:coding")
    assert record.lock_key == "ifom_ttl:active:coding"
    assert record.proposal.target == "ifom_ttl"


def test_ifom_ttl_wrong_lock_key_raises():
    proposal = _make_ifom_proposal()
    with pytest.raises(ValueError, match="'ifom_ttl:'"):
        _make_record(proposal, lock_key="synapse_weight:category:coding")


def test_ifom_ttl_all_ttl_type_lock_keys():
    for ttl_type in ("active", "paused", "completed", "low_priority"):
        proposal = _make_ifom_proposal(ttl_type=ttl_type)
        lock_key = f"ifom_ttl:{ttl_type}:coding"
        record = _make_record(proposal, lock_key=lock_key)
        assert record.lock_key == lock_key


# ---------------------------------------------------------------------------
# Target-aware bounds checks (weight_min / weight_max used for both)
# ---------------------------------------------------------------------------


def test_synapse_weight_previous_value_in_bounds():
    proposal = _make_synapse_proposal()
    # valid: [0.1, 1.0]
    record = _make_record(
        proposal,
        lock_key="synapse_weight:category:coding",
        previous_value=0.5,
        new_value=0.55,
        applied_delta=0.05,
        weight_min=0.1,
        weight_max=1.0,
    )
    assert record.previous_value == 0.5


def test_ifom_ttl_previous_value_in_bounds():
    proposal = _make_ifom_proposal()
    # valid: [60, 86400]
    record = _make_record(
        proposal,
        lock_key="ifom_ttl:active:coding",
        previous_value=3600.0,
        new_value=3720.0,
        applied_delta=120.0,
        weight_min=60.0,
        weight_max=86400.0,
    )
    assert record.previous_value == 3600.0


def test_ifom_ttl_previous_value_out_of_bounds_raises():
    proposal = _make_ifom_proposal()
    with pytest.raises(ValueError, match="previous_value"):
        _make_record(
            proposal,
            lock_key="ifom_ttl:active:coding",
            previous_value=10.0,  # below ttl_min=60.0
            new_value=130.0,
            applied_delta=120.0,
            weight_min=60.0,
            weight_max=86400.0,
        )


# ---------------------------------------------------------------------------
# Common invariants apply regardless of target
# ---------------------------------------------------------------------------


def test_applied_delta_consistency_check():
    proposal = _make_ifom_proposal()
    # previous_value + applied_delta != new_value → should raise
    with pytest.raises(ValueError, match="new_value"):
        RPEMutationRecord(
            proposal=proposal,
            previous_value=3600.0,
            applied_delta=120.0,
            new_value=3800.0,  # should be 3720
            applied_at=1000.0,
            rollback_id=proposal.rollback_id,
            lock_key="ifom_ttl:active:coding",
            weight_min=60.0,
            weight_max=86400.0,
        )


def test_rollback_id_mismatch_raises():
    proposal = _make_ifom_proposal()
    other_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="rollback_id"):
        RPEMutationRecord(
            proposal=proposal,
            previous_value=3600.0,
            applied_delta=120.0,
            new_value=3720.0,
            applied_at=1000.0,
            rollback_id=other_id,  # mismatch
            lock_key="ifom_ttl:active:coding",
            weight_min=60.0,
            weight_max=86400.0,
        )


def test_rollback_status_invalid_raises():
    proposal = _make_ifom_proposal()
    with pytest.raises(ValueError, match="rollback_status"):
        RPEMutationRecord(
            proposal=proposal,
            previous_value=3600.0,
            applied_delta=120.0,
            new_value=3720.0,
            applied_at=1000.0,
            rollback_id=proposal.rollback_id,
            lock_key="ifom_ttl:active:coding",
            rollback_status="invalid",  # type: ignore[arg-type]
            weight_min=60.0,
            weight_max=86400.0,
        )

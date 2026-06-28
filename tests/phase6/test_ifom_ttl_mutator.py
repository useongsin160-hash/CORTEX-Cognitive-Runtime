"""Phase 6 STEP 4 — IFOMTTLMutator unit tests.

Tests sync read/write/rollback of IFOM TTL overrides.
"""
from __future__ import annotations

import uuid

import pytest

from app.rpe.ifom_store import InMemoryIFOMTTLOverrideStore
from app.rpe.models import (
    DryRunConfig,
    RPEContext,
    RPEDecision,
    RPEMutationRecord,
    RPEProposal,
    RPEReward,
)
from app.rpe.mutators import IFOMTTLMutator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    session_id: str = "sess-1",
    category: str = "coding",
    trace_id: str = "tr-1",
) -> RPEDecision:
    reward = RPEReward(
        source="heuristic",
        expected_reward=0.5,
        actual_reward=0.8,
        confidence=0.7,
    )
    ctx = RPEContext(trace_id=trace_id, session_id=session_id, category=category)
    return RPEDecision(reward=reward, context=ctx)


def _make_proposal(
    decision: RPEDecision | None = None,
    ttl_type: str = "active",
    category: str = "coding",
    proposed_delta: float = 63.0,
    max_delta: float = 300.0,
    rollback_id: str | None = None,
) -> RPEProposal:
    if decision is None:
        decision = _make_decision()
    rid = rollback_id or str(uuid.uuid4())
    target_key = f"{ttl_type}:{category}"
    return RPEProposal(
        decision=decision,
        target="ifom_ttl",
        target_key=target_key,
        current_value=None,
        proposed_delta=proposed_delta,
        proposed_value=None,
        max_delta=max_delta,
        rollback_id=rid,
        confidence=decision.reward.confidence,
        applied=False,
    )


def _make_mutator(
    ttl_min: float = 60.0,
    ttl_max: float = 86400.0,
    initial: dict | None = None,
) -> tuple[IFOMTTLMutator, InMemoryIFOMTTLOverrideStore]:
    store = InMemoryIFOMTTLOverrideStore(initial=initial)
    mutator = IFOMTTLMutator(store=store, ttl_min=ttl_min, ttl_max=ttl_max)
    return mutator, store


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_zero_ttl_min_raises():
    store = InMemoryIFOMTTLOverrideStore()
    with pytest.raises(ValueError, match="ttl_min"):
        IFOMTTLMutator(store=store, ttl_min=0.0)


def test_constructor_ttl_max_less_than_min_raises():
    store = InMemoryIFOMTTLOverrideStore()
    with pytest.raises(ValueError, match="ttl_max"):
        IFOMTTLMutator(store=store, ttl_min=3600.0, ttl_max=1800.0)


def test_constructor_valid():
    mutator, _ = _make_mutator(ttl_min=60.0, ttl_max=86400.0)
    assert mutator is not None


# ---------------------------------------------------------------------------
# read_current_ttl
# ---------------------------------------------------------------------------


def test_read_current_ttl_none_when_no_override():
    mutator, store = _make_mutator()
    result = mutator.read_current_ttl("sess-1", "active:coding")
    assert result is None


def test_read_current_ttl_returns_override_seconds():
    mutator, store = _make_mutator()
    store.set("sess-1", "coding", "active", override_seconds=5400.0)
    result = mutator.read_current_ttl("sess-1", "active:coding")
    assert result == 5400.0


def test_read_current_ttl_scoped_by_session():
    mutator, store = _make_mutator()
    store.set("sess-1", "coding", "active", 3600.0)
    store.set("sess-2", "coding", "active", 7200.0)
    assert mutator.read_current_ttl("sess-1", "active:coding") == 3600.0
    assert mutator.read_current_ttl("sess-2", "active:coding") == 7200.0


def test_read_current_ttl_scoped_by_ttl_type():
    mutator, store = _make_mutator()
    store.set("sess-1", "coding", "active", 3600.0)
    store.set("sess-1", "coding", "paused", 1800.0)
    assert mutator.read_current_ttl("sess-1", "active:coding") == 3600.0
    assert mutator.read_current_ttl("sess-1", "paused:coding") == 1800.0


# ---------------------------------------------------------------------------
# apply_mutation
# ---------------------------------------------------------------------------


def test_apply_mutation_returns_record():
    mutator, store = _make_mutator()
    proposal = _make_proposal(proposed_delta=300.0)
    lock_key = "ifom_ttl:active:coding"
    record = mutator.apply_mutation(
        proposal=proposal,
        previous_value=3600.0,
        lock_key=lock_key,
    )
    assert isinstance(record, RPEMutationRecord)


def test_apply_mutation_writes_to_store():
    mutator, store = _make_mutator()
    proposal = _make_proposal(proposed_delta=300.0)
    mutator.apply_mutation(proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding")
    override = store.read_override("sess-1", "coding", "active")
    assert override is not None
    assert abs(override.override_seconds - 3900.0) < 1e-6


def test_apply_mutation_record_values():
    mutator, store = _make_mutator()
    proposal = _make_proposal(proposed_delta=300.0)
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding"
    )
    assert record.previous_value == 3600.0
    assert abs(record.new_value - 3900.0) < 1e-6
    assert abs(record.applied_delta - 300.0) < 1e-6
    assert record.lock_key == "ifom_ttl:active:coding"
    assert record.rollback_id == proposal.rollback_id


def test_apply_mutation_clamps_to_max():
    mutator, store = _make_mutator(ttl_min=60.0, ttl_max=4000.0)
    proposal = _make_proposal(proposed_delta=300.0, max_delta=300.0)
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=3900.0, lock_key="ifom_ttl:active:coding"
    )
    assert record.new_value == 4000.0
    assert abs(record.applied_delta - 100.0) < 1e-6


def test_apply_mutation_clamps_to_min():
    mutator, store = _make_mutator(ttl_min=200.0, ttl_max=86400.0)
    proposal = _make_proposal(proposed_delta=-300.0, max_delta=300.0)
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=300.0, lock_key="ifom_ttl:active:coding"
    )
    assert record.new_value == 200.0
    assert abs(record.applied_delta - (-100.0)) < 1e-6


def test_apply_mutation_previous_out_of_bounds_raises():
    mutator, store = _make_mutator(ttl_min=60.0, ttl_max=86400.0)
    proposal = _make_proposal()
    with pytest.raises(ValueError, match="previous_value"):
        mutator.apply_mutation(
            proposal=proposal, previous_value=10.0, lock_key="ifom_ttl:active:coding"
        )


def test_apply_mutation_none_session_raises():
    mutator, store = _make_mutator()
    decision = _make_decision(session_id=None)  # type: ignore[arg-type]
    proposal = _make_proposal(decision=decision)
    with pytest.raises(ValueError, match="session_id"):
        mutator.apply_mutation(
            proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding"
        )


def test_apply_mutation_mismatch_flag():
    mutator, store = _make_mutator()
    proposal = _make_proposal()
    record = mutator.apply_mutation(
        proposal=proposal,
        previous_value=3600.0,
        lock_key="ifom_ttl:active:coding",
        current_value_mismatch=True,
    )
    assert record.current_value_mismatch is True


def test_apply_mutation_weight_bounds_set_to_ttl_bounds():
    mutator, store = _make_mutator(ttl_min=60.0, ttl_max=86400.0)
    proposal = _make_proposal()
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding"
    )
    assert record.weight_min == 60.0
    assert record.weight_max == 86400.0


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def test_rollback_returns_rolled_back_record():
    mutator, store = _make_mutator()
    proposal = _make_proposal(proposed_delta=300.0)
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding"
    )
    rolled_back = mutator.rollback(record)
    assert rolled_back.rollback_status == "rolled_back"


def test_rollback_restores_previous_value_in_store():
    mutator, store = _make_mutator()
    proposal = _make_proposal(proposed_delta=300.0)
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding"
    )
    mutator.rollback(record)
    override = store.read_override("sess-1", "coding", "active")
    # Should have been restored to 3600.0
    assert override is not None
    assert abs(override.override_seconds - 3600.0) < 1e-6


def test_rollback_record_delta_is_reverse():
    mutator, store = _make_mutator()
    proposal = _make_proposal(proposed_delta=300.0)
    record = mutator.apply_mutation(
        proposal=proposal, previous_value=3600.0, lock_key="ifom_ttl:active:coding"
    )
    rolled_back = mutator.rollback(record)
    # reverse_delta = prev - new = 3600 - 3900 = -300
    assert abs(rolled_back.applied_delta - (-300.0)) < 1e-6


def test_rollback_none_session_raises():
    mutator, store = _make_mutator()
    decision = _make_decision(session_id=None)  # type: ignore[arg-type]
    proposal = _make_proposal(decision=decision)
    # Build a fake record with None session
    record = RPEMutationRecord(
        proposal=proposal,
        previous_value=3600.0,
        applied_delta=300.0,
        new_value=3900.0,
        applied_at=1000.0,
        rollback_id=proposal.rollback_id,
        lock_key="ifom_ttl:active:coding",
        weight_min=60.0,
        weight_max=86400.0,
    )
    with pytest.raises(ValueError, match="session_id"):
        mutator.rollback(record)

"""Phase 6 STEP 3.1 — RPEMutationRecord tests."""

from __future__ import annotations

import time
import uuid
from dataclasses import FrozenInstanceError

import pytest

from app.rpe.models import (
    RPEContext,
    RPEDecision,
    RPEMutationRecord,
    RPEProposal,
    RPEReward,
)


def _proposal(
    *,
    confidence: float = 0.5,
    actual_reward: float = 0.85,
    proposed_delta: float = 0.05,
    max_delta: float = 0.1,
    target_key: str = "category:coding",
    rollback_id: str | None = None,
    current_value: float | None = 0.5,
    proposed_value: float | None = 0.55,
) -> RPEProposal:
    reward = RPEReward(
        source="mock",
        expected_reward=0.5,
        actual_reward=actual_reward,
        confidence=confidence,
    )
    context = RPEContext(trace_id="trace-mr", session_id="sess-1", category="coding")
    decision = RPEDecision(reward=reward, context=context)
    return RPEProposal(
        decision=decision,
        target="synapse_weight",
        target_key=target_key,
        current_value=current_value,
        proposed_delta=proposed_delta,
        proposed_value=proposed_value,
        max_delta=max_delta,
        rollback_id=rollback_id or str(uuid.uuid4()),
        confidence=confidence,
        applied=False,
    )


def _record(**overrides) -> RPEMutationRecord:
    proposal = overrides.pop("proposal", _proposal())
    defaults = {
        "previous_value": 0.5,
        "applied_delta": 0.05,
        "new_value": 0.55,
        "applied_at": time.monotonic(),
        "rollback_id": proposal.rollback_id,
        "lock_key": f"synapse_weight:{proposal.target_key}",
        "expires_at": None,
        "rollback_status": "available",
        "weight_min": 0.1,
        "weight_max": 1.0,
        "current_value_mismatch": False,
    }
    defaults.update(overrides)
    return RPEMutationRecord(proposal=proposal, **defaults)


class TestBasic:
    def test_valid_construction(self) -> None:
        r = _record()
        assert r.rollback_status == "available"
        assert r.previous_value == 0.5
        assert r.new_value == 0.55
        assert r.applied_delta == pytest.approx(0.05)

    def test_frozen(self) -> None:
        r = _record()
        with pytest.raises(FrozenInstanceError):
            r.rollback_status = "rolled_back"  # type: ignore[misc]


class TestInvariants:
    def test_invalid_rollback_status_raises(self) -> None:
        with pytest.raises(ValueError, match="rollback_status"):
            _record(rollback_status="weird")  # type: ignore[arg-type]

    def test_applied_delta_exceeds_max_raises(self) -> None:
        # max_delta=0.1 in proposal; applied_delta=0.2 → ValueError
        proposal = _proposal(max_delta=0.1)
        with pytest.raises(ValueError, match="applied_delta"):
            _record(
                proposal=proposal,
                previous_value=0.5,
                applied_delta=0.2,
                new_value=0.7,
            )

    def test_rollback_id_mismatch_raises(self) -> None:
        p = _proposal()
        with pytest.raises(ValueError, match="rollback_id"):
            _record(proposal=p, rollback_id=str(uuid.uuid4()))

    def test_previous_value_out_of_bounds(self) -> None:
        with pytest.raises(ValueError, match="previous_value"):
            _record(previous_value=0.05)

    def test_new_value_out_of_bounds(self) -> None:
        # prev=1.0, applied_delta=0.1 (within max_delta=0.1), new=1.1 (out)
        with pytest.raises(ValueError, match="new_value"):
            _record(previous_value=1.0, applied_delta=0.1, new_value=1.1)

    def test_consistency_violation(self) -> None:
        # previous_value 0.5 + applied_delta 0.05 != new_value 0.7
        with pytest.raises(ValueError, match="new_value"):
            _record(previous_value=0.5, applied_delta=0.05, new_value=0.7)

    def test_lock_key_must_start_with_prefix(self) -> None:
        with pytest.raises(ValueError, match="lock_key"):
            _record(lock_key="other_prefix:category:coding")


class TestRollbackStatuses:
    def test_available(self) -> None:
        r = _record(rollback_status="available")
        assert r.rollback_status == "available"

    def test_rolled_back(self) -> None:
        # rolled_back record: previous_value=new_state_before_rollback,
        # new_value=restored_value. applied_delta is the reverse.
        proposal = _proposal()
        rec = RPEMutationRecord(
            proposal=proposal,
            previous_value=0.55,
            applied_delta=-0.05,
            new_value=0.5,
            applied_at=time.monotonic(),
            rollback_id=proposal.rollback_id,
            lock_key=f"synapse_weight:{proposal.target_key}",
            expires_at=None,
            rollback_status="rolled_back",
        )
        assert rec.rollback_status == "rolled_back"

    def test_expired(self) -> None:
        r = _record(rollback_status="expired")
        assert r.rollback_status == "expired"


class TestExpiresAt:
    def test_default_none(self) -> None:
        r = _record()
        assert r.expires_at is None

    def test_with_expires_at(self) -> None:
        future = time.monotonic() + 60.0
        r = _record(expires_at=future)
        assert r.expires_at == pytest.approx(future)


class TestMismatchFlag:
    def test_default_false(self) -> None:
        r = _record()
        assert r.current_value_mismatch is False

    def test_set_true(self) -> None:
        r = _record(current_value_mismatch=True)
        assert r.current_value_mismatch is True

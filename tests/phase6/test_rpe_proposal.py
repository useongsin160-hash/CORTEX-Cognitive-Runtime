"""Phase 6 STEP 2 — RPEProposal model tests."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError

import pytest

from app.rpe.models import RPEContext, RPEDecision, RPEProposal, RPEReward


def _ctx(trace_id: str = "trace-1", category: str = "coding") -> RPEContext:
    return RPEContext(trace_id=trace_id, category=category)


def _reward(confidence: float = 0.3) -> RPEReward:
    return RPEReward(
        source="mock",
        expected_reward=0.5,
        actual_reward=0.8,
        confidence=confidence,
    )


def _decision(confidence: float = 0.3) -> RPEDecision:
    return RPEDecision(reward=_reward(confidence), context=_ctx())


def _valid_rollback_id() -> str:
    return str(uuid.uuid4())


def _proposal(**overrides) -> RPEProposal:
    decision = overrides.pop("decision", _decision())
    defaults = {
        "target": "synapse_weight",
        "target_key": "category:coding",
        "current_value": 0.5,
        "proposed_delta": 0.018,
        "proposed_value": 0.518,
        "max_delta": 0.1,
        "rollback_id": _valid_rollback_id(),
        "confidence": decision.reward.confidence,
        "applied": False,
    }
    defaults.update(overrides)
    return RPEProposal(decision=decision, **defaults)


class TestRPEProposalBasic:
    def test_valid_construction(self) -> None:
        p = _proposal()
        assert p.target == "synapse_weight"
        assert p.applied is False
        assert p.target_key == "category:coding"
        assert p.current_value == 0.5
        assert p.proposed_delta == pytest.approx(0.018)
        assert p.proposed_value == pytest.approx(0.518)

    def test_frozen(self) -> None:
        p = _proposal()
        with pytest.raises(FrozenInstanceError):
            p.applied = True  # type: ignore[misc]

    def test_no_current_no_proposed(self) -> None:
        p = _proposal(current_value=None, proposed_value=None)
        assert p.current_value is None
        assert p.proposed_value is None


class TestRPEProposalInvariants:
    def test_applied_true_raises(self) -> None:
        with pytest.raises(ValueError, match="applied"):
            _proposal(applied=True)

    def test_truly_unknown_target_raises(self) -> None:
        # STEP 4: target must be in {"synapse_weight", "ifom_ttl"}.
        # Unknown targets still raise ValueError.
        with pytest.raises(ValueError, match="STEP 4 invariant"):
            _proposal(target="pfc_timeout")  # type: ignore[arg-type]

    def test_ifom_ttl_target_accepted(self) -> None:
        # STEP 4: "ifom_ttl" is now a valid target.
        p = _proposal(target="ifom_ttl")
        assert p.target == "ifom_ttl"

    def test_proposed_delta_exceeds_max_delta_raises(self) -> None:
        with pytest.raises(ValueError, match="max_delta"):
            _proposal(proposed_delta=0.2, max_delta=0.1)

    def test_negative_proposed_delta_exceeds_max_delta_raises(self) -> None:
        with pytest.raises(ValueError, match="max_delta"):
            _proposal(proposed_delta=-0.2, max_delta=0.1)

    def test_max_delta_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_delta"):
            _proposal(proposed_delta=0.0, max_delta=0.0)

    def test_invalid_rollback_id_raises(self) -> None:
        with pytest.raises(ValueError, match="rollback_id"):
            _proposal(rollback_id="not-a-uuid")

    def test_non_uuid4_rollback_id_raises(self) -> None:
        # UUID v1 is not v4.
        import uuid as _uuid
        uid = str(_uuid.uuid1())
        with pytest.raises(ValueError, match="rollback_id"):
            _proposal(rollback_id=uid)

    def test_confidence_mismatch_raises(self) -> None:
        decision = _decision(confidence=0.3)
        with pytest.raises(ValueError, match="confidence"):
            _proposal(decision=decision, confidence=0.5)

    def test_current_none_proposed_not_none_raises(self) -> None:
        with pytest.raises(ValueError, match="None when current_value is None"):
            _proposal(current_value=None, proposed_value=0.5)

    def test_current_present_proposed_none_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be None"):
            _proposal(current_value=0.5, proposed_value=None)


class TestRPEProposalValues:
    def test_proposed_value_consistency(self) -> None:
        p = _proposal(current_value=0.5, proposed_delta=0.02, proposed_value=0.52)
        assert p.proposed_value == pytest.approx(0.52)

    def test_proposed_delta_at_max_boundary(self) -> None:
        p = _proposal(proposed_delta=0.1, max_delta=0.1)
        assert p.proposed_delta == pytest.approx(0.1)

    def test_proposed_delta_at_negative_boundary(self) -> None:
        p = _proposal(proposed_delta=-0.1, max_delta=0.1, proposed_value=0.4)
        assert p.proposed_delta == pytest.approx(-0.1)

    def test_confidence_matches_decision(self) -> None:
        decision = _decision(confidence=0.5)
        p = _proposal(decision=decision, confidence=0.5)
        assert p.confidence == decision.reward.confidence

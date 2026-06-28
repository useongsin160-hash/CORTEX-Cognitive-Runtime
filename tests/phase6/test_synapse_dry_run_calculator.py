"""Phase 6 STEP 2 — SynapseWeightDryRunCalculator tests."""

from __future__ import annotations

import uuid

import pytest

from app.rpe.calculators import SynapseWeightDryRunCalculator
from app.rpe.models import DryRunConfig, RPEContext, RPEDecision, RPEReward


def _ctx(
    trace_id: str = "trace-calc",
    category: str | None = "coding",
) -> RPEContext:
    return RPEContext(trace_id=trace_id, category=category)


def _decision(
    prediction_error_target: float = 0.0,
    confidence: float = 0.3,
    category: str | None = "coding",
) -> RPEDecision:
    """Build a decision with approximate prediction_error.

    We set actual_reward = 0.5 + prediction_error_target.
    Works when abs(prediction_error_target) <= 0.5.
    """
    actual = min(max(0.5 + prediction_error_target, 0.0), 1.0)
    reward = RPEReward(
        source="mock",
        expected_reward=0.5,
        actual_reward=actual,
        confidence=confidence,
    )
    return RPEDecision(reward=reward, context=_ctx(category=category))


def _calc(**kwargs) -> SynapseWeightDryRunCalculator:
    return SynapseWeightDryRunCalculator(**kwargs)


class TestDeltaFormula:
    def test_positive_signal(self) -> None:
        # pe=+0.6, conf=0.3, max=0.1 → 0.6*0.3*0.1 = 0.018
        calc = _calc()
        d = _decision(prediction_error_target=0.3, confidence=0.3)  # actual=0.8, pe≈0.3
        # Recalculate: actual=0.8, expected=0.5, pe=0.3, conf=0.3, max=0.1
        # proposed_delta = 0.3 * 0.3 * 0.1 = 0.009
        proposal = calc.compute_proposal(d, current_value=None)
        assert proposal is not None
        # pe = 0.3 exactly (actual=0.8 - expected=0.5)
        assert proposal.proposed_delta == pytest.approx(0.3 * 0.3 * 0.1, abs=1e-9)

    def test_positive_signal_full_mock_values(self) -> None:
        # pe=+0.6, conf=0.3, max=0.1 → 0.018
        reward = RPEReward(source="mock", expected_reward=0.3, actual_reward=0.9, confidence=0.3)
        d = RPEDecision(reward=reward, context=_ctx())
        calc = _calc()
        proposal = calc.compute_proposal(d)
        assert proposal is not None
        assert proposal.proposed_delta == pytest.approx(0.6 * 0.3 * 0.1, abs=1e-9)

    def test_max_clamp(self) -> None:
        # pe=+1.0, conf=1.0, max=0.1 → clamped to 0.1
        reward = RPEReward(source="mock", expected_reward=0.0, actual_reward=1.0, confidence=1.0)
        d = RPEDecision(reward=reward, context=_ctx())
        calc = _calc()
        proposal = calc.compute_proposal(d)
        assert proposal is not None
        assert proposal.proposed_delta == pytest.approx(0.1)

    def test_negative_signal(self) -> None:
        # pe=-0.5, conf=0.5, max=0.1 → -0.025
        reward = RPEReward(source="mock", expected_reward=0.75, actual_reward=0.25, confidence=0.5)
        d = RPEDecision(reward=reward, context=_ctx())
        calc = _calc()
        proposal = calc.compute_proposal(d)
        assert proposal is not None
        assert proposal.proposed_delta == pytest.approx(-0.5 * 0.5 * 0.1, abs=1e-9)

    def test_zero_signal(self) -> None:
        reward = RPEReward(source="mock", expected_reward=0.5, actual_reward=0.5, confidence=0.5)
        d = RPEDecision(reward=reward, context=_ctx())
        calc = _calc()
        proposal = calc.compute_proposal(d)
        assert proposal is not None
        assert proposal.proposed_delta == pytest.approx(0.0)


class TestProposedValue:
    def test_current_value_none_gives_none_proposed(self) -> None:
        d = _decision()
        proposal = _calc().compute_proposal(d, current_value=None)
        assert proposal is not None
        assert proposal.current_value is None
        assert proposal.proposed_value is None

    def test_current_value_gives_proposed_value(self) -> None:
        # pe=0.3, conf=0.3, max=0.1 → delta=0.009
        reward = RPEReward(source="mock", expected_reward=0.5, actual_reward=0.8, confidence=0.3)
        d = RPEDecision(reward=reward, context=_ctx())
        proposal = _calc().compute_proposal(d, current_value=0.5)
        assert proposal is not None
        assert proposal.current_value == 0.5
        assert proposal.proposed_value == pytest.approx(0.5 + 0.3 * 0.3 * 0.1, abs=1e-9)

    def test_upper_clamp(self) -> None:
        # max signal → delta=0.1, current=0.99 → proposed clamped to 1.0
        reward = RPEReward(source="mock", expected_reward=0.0, actual_reward=1.0, confidence=1.0)
        d = RPEDecision(reward=reward, context=_ctx())
        proposal = _calc().compute_proposal(d, current_value=0.99)
        assert proposal is not None
        assert proposal.proposed_value == pytest.approx(1.0)

    def test_lower_clamp(self) -> None:
        # negative signal → delta=-0.025, current=0.11 → proposed clamped to 0.1
        reward = RPEReward(source="mock", expected_reward=0.75, actual_reward=0.25, confidence=0.5)
        d = RPEDecision(reward=reward, context=_ctx())
        proposal = _calc().compute_proposal(d, current_value=0.11)
        assert proposal is not None
        assert proposal.proposed_value == pytest.approx(max(0.1, 0.11 - 0.025), abs=1e-6)


class TestSkipConditions:
    def test_no_category_returns_none(self) -> None:
        d = _decision(category=None)
        proposal = _calc().compute_proposal(d)
        assert proposal is None

    def test_empty_category_returns_none(self) -> None:
        reward = RPEReward(source="mock", expected_reward=0.5, actual_reward=0.5, confidence=0.3)
        d = RPEDecision(reward=reward, context=RPEContext(trace_id="t", category=""))
        proposal = _calc().compute_proposal(d)
        assert proposal is None

    def test_unknown_category_returns_none(self) -> None:
        d = _decision(category="astrophysics")
        proposal = _calc().compute_proposal(d)
        assert proposal is None

    def test_current_value_out_of_lower_bound_returns_none(self) -> None:
        d = _decision()
        proposal = _calc().compute_proposal(d, current_value=0.05)
        assert proposal is None

    def test_current_value_out_of_upper_bound_returns_none(self) -> None:
        d = _decision()
        proposal = _calc().compute_proposal(d, current_value=1.05)
        assert proposal is None

    def test_synapse_weight_not_in_enabled_targets_returns_none(self) -> None:
        # Only way to exclude synapse_weight in STEP 2 config validation fails.
        # Test via monkey-patching internal config directly.
        calc = _calc()
        # Override config with an object that says synapse_weight not enabled.
        # Easiest: create custom config but validation rejects missing synapse_weight.
        # So we directly patch: use config.enabled_targets to simulate.
        d = _decision()
        # Instead, bypass by building a subclassed DryRunConfig (no validation).
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Cfg:
            enabled_targets: tuple = ()
            max_delta: float = 0.1
            require_category: bool = True
            allowed_categories: tuple = ("coding",)
            synapse_weight_min: float = 0.1
            synapse_weight_max: float = 1.0

        calc._config = _Cfg()  # type: ignore[assignment]
        proposal = calc.compute_proposal(d)
        assert proposal is None


class TestAllowedCategories:
    @pytest.mark.parametrize(
        "category",
        [
            "coding",
            "game_design",
            "math_logic",
            "writing",
            "data_analysis",
            "system_design",
            "general",
        ],
    )
    def test_valid_category(self, category: str) -> None:
        d = _decision(category=category)
        proposal = _calc().compute_proposal(d)
        assert proposal is not None
        assert proposal.target_key == f"category:{category}"


class TestTargetKey:
    def test_target_key_format(self) -> None:
        d = _decision(category="math_logic")
        proposal = _calc().compute_proposal(d)
        assert proposal is not None
        assert proposal.target_key == "category:math_logic"


class TestRollbackId:
    def test_rollback_id_is_uuid4(self) -> None:
        d = _decision()
        proposal = _calc().compute_proposal(d)
        assert proposal is not None
        parsed = uuid.UUID(proposal.rollback_id, version=4)
        assert str(parsed) == proposal.rollback_id

    def test_deterministic_rollback_id_factory(self) -> None:
        fixed_id = "12345678-1234-4234-b234-123456789abc"
        calc = _calc(rollback_id_factory=lambda: fixed_id)
        d = _decision()
        proposal = calc.compute_proposal(d)
        assert proposal is not None
        assert proposal.rollback_id == fixed_id

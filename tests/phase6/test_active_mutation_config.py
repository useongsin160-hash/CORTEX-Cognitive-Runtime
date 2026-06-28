"""Phase 6 STEP 3.1 — ActiveMutationConfig tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.rpe.models import ActiveMutationConfig


class TestDefaults:
    def test_switches_default_false(self) -> None:
        # B5: both observe and active gates default off (active default off is
        # the absolute safety invariant).
        cfg = ActiveMutationConfig()
        assert cfg.observe_enabled is False
        assert cfg.active_enabled is False

    def test_min_confidence_default(self) -> None:
        cfg = ActiveMutationConfig()
        assert cfg.min_confidence == 0.5

    def test_min_abs_prediction_error_default(self) -> None:
        cfg = ActiveMutationConfig()
        assert cfg.min_abs_prediction_error == 0.3

    def test_lock_timeout_default(self) -> None:
        cfg = ActiveMutationConfig()
        assert cfg.lock_timeout_ms == 1000.0

    def test_enable_timeout_metadata_default(self) -> None:
        cfg = ActiveMutationConfig()
        assert cfg.enable_timeout_metadata is False

    def test_weight_bounds_default(self) -> None:
        cfg = ActiveMutationConfig()
        assert cfg.synapse_weight_min == 0.1
        assert cfg.synapse_weight_max == 1.0

    def test_frozen(self) -> None:
        cfg = ActiveMutationConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.active_enabled = True  # type: ignore[misc]


class TestValidation:
    def test_min_confidence_out_of_range_low(self) -> None:
        with pytest.raises(ValueError, match="min_confidence"):
            ActiveMutationConfig(min_confidence=-0.1)

    def test_min_confidence_out_of_range_high(self) -> None:
        with pytest.raises(ValueError, match="min_confidence"):
            ActiveMutationConfig(min_confidence=1.5)

    def test_min_abs_prediction_error_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="min_abs_prediction_error"):
            ActiveMutationConfig(min_abs_prediction_error=2.0)

    def test_lock_timeout_zero(self) -> None:
        with pytest.raises(ValueError, match="lock_timeout_ms"):
            ActiveMutationConfig(lock_timeout_ms=0.0)

    def test_lock_timeout_negative(self) -> None:
        with pytest.raises(ValueError, match="lock_timeout_ms"):
            ActiveMutationConfig(lock_timeout_ms=-10.0)

    def test_weight_min_negative(self) -> None:
        with pytest.raises(ValueError, match="synapse_weight_min"):
            ActiveMutationConfig(synapse_weight_min=-0.1)

    def test_weight_max_over_one(self) -> None:
        with pytest.raises(ValueError, match="synapse_weight_max"):
            ActiveMutationConfig(synapse_weight_max=1.5)

    def test_min_ge_max(self) -> None:
        with pytest.raises(ValueError, match="must be <"):
            ActiveMutationConfig(synapse_weight_min=0.5, synapse_weight_max=0.5)

    def test_valid_custom(self) -> None:
        cfg = ActiveMutationConfig(
            observe_enabled=True,
            active_enabled=True,
            min_confidence=0.7,
            min_abs_prediction_error=0.5,
            lock_timeout_ms=500.0,
        )
        assert cfg.observe_enabled is True
        assert cfg.active_enabled is True
        assert cfg.min_confidence == 0.7
        assert cfg.min_abs_prediction_error == 0.5
        assert cfg.lock_timeout_ms == 500.0

"""Phase 6 STEP 2 — DryRunConfig tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.rpe.models import DryRunConfig


class TestDryRunConfigDefaults:
    def test_default_enabled_targets(self) -> None:
        cfg = DryRunConfig()
        assert cfg.enabled_targets == ("synapse_weight",)

    def test_default_max_delta(self) -> None:
        cfg = DryRunConfig()
        assert cfg.max_delta == 0.1

    def test_default_weight_bounds(self) -> None:
        cfg = DryRunConfig()
        assert cfg.synapse_weight_min == 0.1
        assert cfg.synapse_weight_max == 1.0

    def test_default_allowed_categories_count(self) -> None:
        cfg = DryRunConfig()
        assert len(cfg.allowed_categories) == 7

    def test_default_allowed_categories_content(self) -> None:
        cfg = DryRunConfig()
        expected = {
            "coding",
            "game_design",
            "math_logic",
            "writing",
            "data_analysis",
            "system_design",
            "general",
        }
        assert set(cfg.allowed_categories) == expected

    def test_frozen(self) -> None:
        cfg = DryRunConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.max_delta = 0.2  # type: ignore[misc]


class TestDryRunConfigValidation:
    def test_empty_enabled_targets_raises(self) -> None:
        with pytest.raises(ValueError, match="enabled_targets"):
            DryRunConfig(enabled_targets=())

    def test_unknown_target_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown RPETarget"):
            DryRunConfig(enabled_targets=("bogus_target",))  # type: ignore[arg-type]

    def test_ifom_ttl_only_targets_allowed(self) -> None:
        # STEP 4: synapse_weight is no longer required in enabled_targets.
        # ifom_ttl-only config must succeed.
        cfg = DryRunConfig(enabled_targets=("ifom_ttl",))
        assert cfg.enabled_targets == ("ifom_ttl",)

    def test_max_delta_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_delta"):
            DryRunConfig(max_delta=0.0)

    def test_max_delta_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_delta"):
            DryRunConfig(max_delta=-0.1)

    def test_weight_min_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="synapse_weight_min"):
            DryRunConfig(synapse_weight_min=-0.1)

    def test_weight_max_over_one_raises(self) -> None:
        with pytest.raises(ValueError, match="synapse_weight_max"):
            DryRunConfig(synapse_weight_max=1.1)

    def test_min_ge_max_raises(self) -> None:
        with pytest.raises(ValueError, match="must be <"):
            DryRunConfig(synapse_weight_min=0.5, synapse_weight_max=0.5)

    def test_empty_allowed_categories_raises(self) -> None:
        with pytest.raises(ValueError, match="allowed_categories"):
            DryRunConfig(allowed_categories=())

    def test_valid_custom_config(self) -> None:
        cfg = DryRunConfig(
            enabled_targets=("synapse_weight",),
            max_delta=0.05,
            allowed_categories=("coding",),
            synapse_weight_min=0.2,
            synapse_weight_max=0.9,
        )
        assert cfg.max_delta == 0.05
        assert cfg.allowed_categories == ("coding",)

"""Phase 6 STEP 4 — Cross-cutting invariant tests.

Verifies:
1. Production IFOM TTL enabled=False always.
2. IFOMPolicy sync API unchanged.
3. Global IFOMConfig is NEVER mutated.
4. IFOMPolicy.__init__ signature backward compat.
5. RPEMutationPipelineWrapper has ZERO changes.
6. routes.py, swarm.py have ZERO STEP 4 changes.
7. main.py DI includes IFOM TTL objects (disabled-by-default).
8. ActiveMutationConfig IFOM TTL bounds validation.
9. DryRunConfig accepts any non-empty target subset (no synapse_weight required).
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory.ifom import IFOMConfig, IFOMPolicy
from app.rpe.models import ActiveMutationConfig, DryRunConfig


# ---------------------------------------------------------------------------
# Production IFOM TTL disabled-by-default
# ---------------------------------------------------------------------------


def test_production_ifom_ttl_enabled_false(app_client):
    """IFOM TTL active mutation must be disabled in production (active_enabled=False)."""
    config = app_client.app.state.rpe_mutation_service.config
    # B5: the mutation gate is active_enabled (observe_enabled gates observe only).
    assert config.active_enabled is False, (
        "STEP 4 invariant: production active_enabled must be False"
    )


def test_production_ifom_ttl_store_exists(app_client):
    """main.py must expose ifom_ttl_store state."""
    assert hasattr(app_client.app.state, "ifom_ttl_store"), \
        "app.state.ifom_ttl_store must exist"


def test_production_ifom_ttl_mutator_exists(app_client):
    """main.py must expose ifom_ttl_mutator state."""
    assert hasattr(app_client.app.state, "ifom_ttl_mutator"), \
        "app.state.ifom_ttl_mutator must exist"


def test_production_rpe_mutation_service_has_ifom_mutator(app_client):
    """RPEMutationService must have ifom_mutator set."""
    service = app_client.app.state.rpe_mutation_service
    assert service._ifom_mutator is not None, \
        "RPEMutationService._ifom_mutator must not be None in production"


def test_production_ifom_ttl_mutator_is_same_store(app_client):
    """ifom_ttl_mutator._store must be the same object as ifom_ttl_store."""
    store = app_client.app.state.ifom_ttl_store
    mutator = app_client.app.state.ifom_ttl_mutator
    assert mutator._store is store, \
        "ifom_ttl_mutator._store must be the same object as ifom_ttl_store"


# ---------------------------------------------------------------------------
# IFOMPolicy sync API unchanged
# ---------------------------------------------------------------------------


def test_ifom_policy_evaluate_goal_is_sync():
    """evaluate_goal must remain sync (not async)."""
    assert not inspect.iscoroutinefunction(IFOMPolicy.evaluate_goal), \
        "IFOMPolicy.evaluate_goal must be sync"


def test_ifom_policy_cleanup_expired_is_sync():
    assert not inspect.iscoroutinefunction(IFOMPolicy.cleanup_expired), \
        "IFOMPolicy.cleanup_expired must be sync"


def test_ifom_policy_adjust_ttl_with_rpe_hook_is_sync():
    assert not inspect.iscoroutinefunction(IFOMPolicy.adjust_ttl_with_rpe_hook), \
        "IFOMPolicy.adjust_ttl_with_rpe_hook must be sync"


def test_ifom_policy_get_ttl_for_goal_is_sync():
    assert not inspect.iscoroutinefunction(IFOMPolicy._get_ttl_for_goal)


# ---------------------------------------------------------------------------
# IFOMPolicy backward compat (no-arg init still works)
# ---------------------------------------------------------------------------


def test_ifom_policy_no_args_init():
    policy = IFOMPolicy()
    assert policy._config is not None
    assert policy._ttl_override_resolver is None


def test_ifom_policy_config_only_init():
    policy = IFOMPolicy(config=IFOMConfig(active_ttl_seconds=5000.0))
    assert policy._config.active_ttl_seconds == 5000.0
    assert policy._ttl_override_resolver is None


def test_ifom_policy_resolver_init():
    policy = IFOMPolicy(ttl_override_resolver=lambda s, c, t: None)
    assert policy._ttl_override_resolver is not None


# ---------------------------------------------------------------------------
# Global IFOMConfig is NEVER mutated
# ---------------------------------------------------------------------------


def test_ifom_config_is_frozen():
    """IFOMConfig is a frozen dataclass — field assignment raises."""
    config = IFOMConfig()
    with pytest.raises((TypeError, AttributeError)):
        config.active_ttl_seconds = 9999.0  # type: ignore[misc]


def test_ifom_config_default_values_unchanged():
    """IFOMConfig defaults must remain unchanged (STEP 4 adds 0 new fields)."""
    config = IFOMConfig()
    assert config.active_ttl_seconds == 3600.0
    assert config.paused_ttl_seconds == 3600.0
    assert config.completed_ttl_seconds == 600.0
    assert config.low_priority_ttl_seconds == 300.0
    assert config.low_priority_threshold == 0.3


# ---------------------------------------------------------------------------
# ActiveMutationConfig STEP 4 extension
# ---------------------------------------------------------------------------


def test_active_mutation_config_ifom_ttl_defaults():
    cfg = ActiveMutationConfig()
    assert cfg.ifom_ttl_min_seconds == 60.0
    assert cfg.ifom_ttl_max_seconds == 86400.0


def test_active_mutation_config_ifom_ttl_bounds_validated():
    with pytest.raises(ValueError, match="ifom_ttl_min_seconds"):
        ActiveMutationConfig(ifom_ttl_min_seconds=0.0)

    with pytest.raises(ValueError, match="ifom_ttl_max_seconds"):
        ActiveMutationConfig(ifom_ttl_min_seconds=100.0, ifom_ttl_max_seconds=100.0)


# ---------------------------------------------------------------------------
# DryRunConfig STEP 4: no synapse_weight required
# ---------------------------------------------------------------------------


def test_dry_run_config_ifom_ttl_only_allowed():
    """STEP 4: synapse_weight is not required in enabled_targets."""
    cfg = DryRunConfig(enabled_targets=("ifom_ttl",))
    assert "ifom_ttl" in cfg.enabled_targets


def test_dry_run_config_ifom_ttl_bounds_defaults():
    cfg = DryRunConfig()
    assert cfg.ifom_ttl_max_delta == 300.0
    assert cfg.ifom_ttl_min_seconds == 60.0
    assert cfg.ifom_ttl_max_seconds == 86400.0


def test_dry_run_config_ifom_ttl_bounds_validated():
    with pytest.raises(ValueError, match="ifom_ttl_max_delta"):
        DryRunConfig(ifom_ttl_max_delta=0.0)

    with pytest.raises(ValueError, match="ifom_ttl_max_seconds"):
        DryRunConfig(ifom_ttl_min_seconds=1000.0, ifom_ttl_max_seconds=999.0)


# ---------------------------------------------------------------------------
# Pipeline / routes / swarm unchanged
# ---------------------------------------------------------------------------


def test_pipeline_no_step4_changes():
    """RPEMutationPipelineWrapper interface is unchanged in STEP 4."""
    from app.rpe.pipeline import RPEMutationPipelineWrapper
    sig = inspect.signature(RPEMutationPipelineWrapper.__init__)
    # STEP 3.2 params: inner_swarm, dopamine_rpe, mutation_service, logger
    param_names = set(sig.parameters.keys()) - {"self"}
    assert "inner_swarm" in param_names
    assert "dopamine_rpe" in param_names
    assert "mutation_service" in param_names
    assert "logger" in param_names
    # No IFOM-related params added in STEP 4
    assert "ifom" not in " ".join(param_names).lower()


def test_routes_no_step4_changes():
    """routes.py must have ZERO STEP 4 changes (no IFOM imports or references)."""
    import ast
    from pathlib import Path
    filepath = Path(__file__).resolve().parents[2] / "app" / "api" / "routes.py"
    src = filepath.read_text(encoding="utf-8")
    assert "ifom_ttl" not in src, "routes.py must not reference ifom_ttl"
    assert "IFOMTTLMutator" not in src, "routes.py must not import IFOMTTLMutator"

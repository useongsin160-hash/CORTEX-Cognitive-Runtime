"""Phase 6 STEP 3.2 — RPE pipeline DI lifespan tests.

Verifies that create_app() wires all RPE pipeline components onto app.state
with the correct disabled-by-default configuration.
"""

from __future__ import annotations

import pytest

from app.main import create_app
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import ActiveMutationConfig
from app.rpe.mutators import SynapseStoreAdapter, SynapseWeightMutator
from app.rpe.pipeline import RPEMutationPipelineWrapper
from app.rpe.service import RPEMutationService


@pytest.fixture(scope="module")
def app_instance():
    return create_app()


class TestRpePipelineOnState:
    def test_rpe_pipeline_exists(self, app_instance) -> None:
        assert hasattr(app_instance.state, "rpe_pipeline")

    def test_rpe_pipeline_is_correct_type(self, app_instance) -> None:
        assert isinstance(app_instance.state.rpe_pipeline, RPEMutationPipelineWrapper)

    def test_rpe_mutation_service_exists(self, app_instance) -> None:
        assert hasattr(app_instance.state, "rpe_mutation_service")

    def test_rpe_mutation_service_is_correct_type(self, app_instance) -> None:
        assert isinstance(app_instance.state.rpe_mutation_service, RPEMutationService)

    def test_dopamine_rpe_exists(self, app_instance) -> None:
        assert hasattr(app_instance.state, "dopamine_rpe")

    def test_dopamine_rpe_is_correct_type(self, app_instance) -> None:
        assert isinstance(app_instance.state.dopamine_rpe, DopamineRPE)

    def test_rpe_synapse_adapter_exists(self, app_instance) -> None:
        assert hasattr(app_instance.state, "rpe_synapse_adapter")

    def test_rpe_mutator_exists(self, app_instance) -> None:
        assert hasattr(app_instance.state, "rpe_mutator")


class TestDisabledByDefault:
    def test_mutation_service_disabled_by_default(self, app_instance) -> None:
        svc = app_instance.state.rpe_mutation_service
        # B5: production keeps BOTH gates off (capability only — observe flip is
        # deferred to B6). active_enabled=False is the absolute safety invariant.
        assert svc.config.active_enabled is False
        assert svc.config.observe_enabled is False

    def test_mutation_config_is_active_mutation_config(self, app_instance) -> None:
        svc = app_instance.state.rpe_mutation_service
        assert isinstance(svc.config, ActiveMutationConfig)

    def test_pipeline_inner_swarm_is_async_swarm(self, app_instance) -> None:
        pipeline = app_instance.state.rpe_pipeline
        # Inner swarm must be the real AsyncSwarm.
        from app.execution.swarm import AsyncSwarm
        assert isinstance(pipeline._inner_swarm, AsyncSwarm)

    def test_pipeline_uses_same_synapse_store(self, app_instance) -> None:
        """Adapter wraps the same synapse_store as the rest of the app."""
        adapter = app_instance.state.rpe_synapse_adapter
        assert isinstance(adapter, SynapseStoreAdapter)
        # The adapter's wrapped store is the app's synapse_store.
        assert adapter._store is app_instance.state.synapse_store

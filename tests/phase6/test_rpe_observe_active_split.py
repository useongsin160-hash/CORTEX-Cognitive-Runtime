"""B5 — RPE observe/active switch split invariants.

ActiveMutationConfig.enabled is split into two independent gates:
- observe_enabled gates the RPE observe path (RPEMutationPipelineWrapper spawns
  the background observe/dry-run/log task). Pure observation — zero mutation.
- active_enabled gates the actual mutation (RPEMutationService.apply_proposals).
  Default False is the absolute safety invariant.

Mode 1 (confirmed 2026-06-17): observe may run in production while every side
effect stays gated. These tests prove observe_enabled=True never triggers a
mutation, observe_enabled toggles observation both ways, active_enabled is the
ONLY mutation gate, and legacy `enabled` maps to observe_enabled only.

Note: SpinalLogger is a process-wide singleton with a shared trace store, so
every test uses a UNIQUE trace_id to stay isolated.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.rpe.mutators as mutators_mod
import app.rpe.pipeline as pipeline_mod
import app.rpe.service as service_mod
from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import (
    ActiveMutationConfig,
    RPEContext,
    RPEDecision,
    RPEProposal,
    RPEReward,
)
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.pipeline import RPEMutationPipelineWrapper
from app.rpe.service import RPEMutationService
from app.rpe.sources import MockRewardSource


def _events(logger: SpinalLogger, trace_id: str, event_type: str) -> list:
    return [e for e in logger.get_trace(trace_id) if e.event_type == event_type]


# ---------------------------------------------------------------------------
# Pipeline builder (the observe gate lives in the wrapper).
# ---------------------------------------------------------------------------


def _swarm_result() -> SwarmResult:
    return SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="output", tier_used="STANDARD", model_name="mock",
            prompt_tokens=1, completion_tokens=1, finish_reason="stop",
            latency_ms=1.0, ne_applied=False, plan_intent="answer",
        ),
        context_status="ok", planner_status="ok", generator_status="ok",
        total_elapsed_ms=55.0,
    )


def _inner_swarm() -> MagicMock:
    swarm = MagicMock()
    swarm.execute = AsyncMock(return_value=_swarm_result())
    return swarm


def _pipeline(
    *, trace_id: str, observe_enabled: bool, active_enabled: bool
) -> tuple[RPEMutationPipelineWrapper, SpinalLogger, InMemorySynapseWeightStore]:
    logger = SpinalLogger()
    store = InMemorySynapseWeightStore({(trace_id, "coding"): 0.5})
    mutator = SynapseWeightMutator(store=store)
    config = ActiveMutationConfig(
        observe_enabled=observe_enabled,
        active_enabled=active_enabled,
        min_confidence=0.5,
        min_abs_prediction_error=0.3,
    )
    svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
    # Reward signal keyed by trace_id with a real prediction error so observe
    # yields a proposal that reaches the active gate: PE = 0.9 - 0.3 = 0.6.
    rpe = DopamineRPE(
        sources=[MockRewardSource(reward_map={trace_id: (0.3, 0.9)})],
        logger=logger,
    )
    wrapper = RPEMutationPipelineWrapper(
        inner_swarm=_inner_swarm(),
        dopamine_rpe=rpe,
        mutation_service=svc,
        logger=logger,
    )
    return wrapper, logger, store


async def _run(wrapper: RPEMutationPipelineWrapper, trace_id: str) -> None:
    await wrapper.execute(
        TaskContext(trace_id=trace_id, category="coding", difficulty=2),
        trace_id=trace_id,
        session_id=trace_id,
    )
    # Background RPE task is fire-and-forget — let it settle.
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Mode 1 core: observe_enabled=True + active_enabled=False
#   → observe runs (logs) but mutation is ZERO.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_on_active_off_observes_but_never_mutates() -> None:
    tid = "b5-mode1"
    wrapper, logger, store = _pipeline(
        trace_id=tid, observe_enabled=True, active_enabled=False
    )
    await _run(wrapper, tid)

    # Observe ran: the background task was scheduled and emitted observations.
    assert _events(logger, tid, "rpe.observed"), "observe must run when observe_enabled=True"
    # A proposal reached the (closed) active gate and was skipped — not applied.
    assert _events(logger, tid, "rpe.dry_run_proposed"), "observe must produce a proposal"
    assert _events(logger, tid, "rpe.active_skipped"), "active gate must skip (disabled)"
    # Mutation is ZERO: no apply event, store untouched.
    assert _events(logger, tid, "rpe.active_applied") == [], "no mutation may occur"
    assert await store.read_weight(tid, "coding") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# observe_enabled toggles observation both ways.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_off_stops_observation_entirely() -> None:
    tid = "b5-observe-off"
    wrapper, logger, store = _pipeline(
        trace_id=tid, observe_enabled=False, active_enabled=False
    )
    await _run(wrapper, tid)

    # No background task is created → no observation at all.
    assert len(wrapper._background_tasks) == 0
    assert _events(logger, tid, "rpe.observed") == []
    assert _events(logger, tid, "rpe.dry_run_proposed") == []
    assert _events(logger, tid, "rpe.active_applied") == []
    assert await store.read_weight(tid, "coding") == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_observe_on_schedules_background_task() -> None:
    tid = "b5-sched"
    wrapper, _, _ = _pipeline(trace_id=tid, observe_enabled=True, active_enabled=False)
    await wrapper.execute(
        TaskContext(trace_id=tid, category="coding", difficulty=2),
        trace_id=tid,
        session_id=tid,
    )
    # The observe task was scheduled (before it settles/clears).
    assert len(wrapper._background_tasks) == 1
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# active_enabled is the ONLY mutation gate (service level, direct apply).
# ---------------------------------------------------------------------------


def _proposal(*, trace_id: str, confidence: float = 0.8, proposed_delta: float = 0.05) -> RPEProposal:
    reward = RPEReward(
        source="mock", expected_reward=0.3, actual_reward=0.9, confidence=confidence
    )
    ctx = RPEContext(trace_id=trace_id, session_id=trace_id, category="coding")
    decision = RPEDecision(reward=reward, context=ctx)
    return RPEProposal(
        decision=decision,
        target="synapse_weight",
        target_key="category:coding",
        current_value=0.5,
        proposed_delta=proposed_delta,
        proposed_value=0.5 + proposed_delta,
        max_delta=0.1,
        rollback_id=str(uuid.uuid4()),
        confidence=confidence,
    )


def _service(
    *, trace_id: str, active_enabled: bool
) -> tuple[RPEMutationService, InMemorySynapseWeightStore, SpinalLogger]:
    logger = SpinalLogger()
    store = InMemorySynapseWeightStore({(trace_id, "coding"): 0.5})
    mutator = SynapseWeightMutator(store=store)
    svc = RPEMutationService(
        mutator=mutator,
        logger=logger,
        config=ActiveMutationConfig(active_enabled=active_enabled),
    )
    return svc, store, logger


@pytest.mark.asyncio
async def test_active_disabled_blocks_mutation() -> None:
    tid = "b5-act-off"
    svc, store, logger = _service(trace_id=tid, active_enabled=False)
    records = await svc.apply_proposals([_proposal(trace_id=tid)])
    assert records == []
    assert _events(logger, tid, "rpe.active_skipped"), "must log skip with active off"
    assert _events(logger, tid, "rpe.active_applied") == []
    assert await store.read_weight(tid, "coding") == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_active_enabled_is_what_permits_mutation() -> None:
    tid = "b5-act-on"
    svc, store, logger = _service(trace_id=tid, active_enabled=True)
    records = await svc.apply_proposals([_proposal(trace_id=tid)])
    assert len(records) == 1, "active_enabled=True must permit the mutation"
    assert _events(logger, tid, "rpe.active_applied"), "must log apply with active on"
    assert await store.read_weight(tid, "coding") == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# Legacy `enabled` back-compat: maps to observe_enabled ONLY (never active).
# ---------------------------------------------------------------------------


def test_legacy_enabled_true_maps_to_observe_only() -> None:
    cfg = ActiveMutationConfig(enabled=True)
    assert cfg.observe_enabled is True
    assert cfg.active_enabled is False  # legacy `enabled` can NEVER turn on mutation


def test_legacy_enabled_false_leaves_both_off() -> None:
    cfg = ActiveMutationConfig(enabled=False)
    assert cfg.observe_enabled is False
    assert cfg.active_enabled is False


def test_defaults_are_both_off() -> None:
    cfg = ActiveMutationConfig()
    assert cfg.observe_enabled is False
    assert cfg.active_enabled is False


def test_legacy_enabled_with_observe_enabled_is_contradiction() -> None:
    with pytest.raises(ValueError, match="legacy `enabled`"):
        ActiveMutationConfig(enabled=True, observe_enabled=True)


# ---------------------------------------------------------------------------
# Structural guarantee: the two flags gate distinct layers.
#   observe_enabled is READ only in the pipeline (observe layer).
#   active_enabled is READ in the service (mutation layer).
#   The mutation layer (service + mutators) never reads observe_enabled.
# (Matched on attribute access `.observe_enabled` / `.active_enabled` so doc/
#  comment mentions of the bare word don't produce false positives.)
# ---------------------------------------------------------------------------


def test_mutation_layer_never_reads_observe_enabled() -> None:
    service_src = inspect.getsource(service_mod)
    mutators_src = inspect.getsource(mutators_mod)
    assert ".observe_enabled" not in service_src, (
        "service.py (mutation layer) must not consult observe_enabled"
    )
    assert ".observe_enabled" not in mutators_src, (
        "mutators.py (mutation layer) must not consult observe_enabled"
    )
    assert ".active_enabled" in service_src, "service.py must gate on active_enabled"


def test_observe_gate_lives_in_pipeline() -> None:
    pipeline_src = inspect.getsource(pipeline_mod)
    assert ".observe_enabled" in pipeline_src, (
        "pipeline.py must gate observe on observe_enabled"
    )
    assert ".active_enabled" not in pipeline_src, (
        "pipeline.py must not consult active_enabled (mutation gate is downstream)"
    )

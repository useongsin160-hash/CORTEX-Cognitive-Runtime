"""Phase 6 STEP 4 — RPEMutationService IFOM TTL dispatch tests.

Tests that the service correctly dispatches ifom_ttl proposals to
IFOMTTLMutator, and handles error/no-mutator cases.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.logging import get_spinal_logger
from app.rpe.ifom_store import InMemoryIFOMTTLOverrideStore
from app.rpe.models import (
    ActiveMutationConfig,
    RPEContext,
    RPEDecision,
    RPEMutationRecord,
    RPEProposal,
    RPEReward,
)
from app.rpe.mutators import (
    IFOMTTLMutator,
    InMemorySynapseWeightStore,
    SynapseWeightMutator,
)
from app.rpe.service import RPEMutationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    trace_id: str = "tr-1",
    session_id: str = "sess-1",
    category: str | None = "coding",
    expected_reward: float = 0.1,
    actual_reward: float = 0.9,
    confidence: float = 0.8,
) -> RPEDecision:
    reward = RPEReward(
        source="heuristic",
        expected_reward=expected_reward,
        actual_reward=actual_reward,
        confidence=confidence,
    )
    ctx = RPEContext(
        trace_id=trace_id, session_id=session_id, category=category
    )
    return RPEDecision(reward=reward, context=ctx)


def _make_ifom_proposal(
    decision: RPEDecision | None = None,
    ttl_type: str = "active",
    category: str = "coding",
    proposed_delta: float = 120.0,
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


def _make_service(
    enabled: bool = True,
    initial_ttl: dict | None = None,
    ttl_min: float = 60.0,
    ttl_max: float = 86400.0,
) -> tuple[RPEMutationService, InMemoryIFOMTTLOverrideStore, InMemorySynapseWeightStore]:
    weight_store = InMemorySynapseWeightStore()
    synapse_mutator = SynapseWeightMutator(store=weight_store)
    ttl_store = InMemoryIFOMTTLOverrideStore()
    if initial_ttl:
        for (sess, cat, ttl_type), val in initial_ttl.items():
            ttl_store.set(sess, cat, ttl_type, val)
    ifom_mutator = IFOMTTLMutator(store=ttl_store, ttl_min=ttl_min, ttl_max=ttl_max)
    service = RPEMutationService(
        mutator=synapse_mutator,
        logger=get_spinal_logger(),
        config=ActiveMutationConfig(active_enabled=enabled),
        ifom_mutator=ifom_mutator,
    )
    return service, ttl_store, weight_store


# ---------------------------------------------------------------------------
# Disabled service skips IFOM TTL proposals
# ---------------------------------------------------------------------------


def test_disabled_service_skips_ifom_ttl():
    service, ttl_store, _ = _make_service(enabled=False)
    proposal = _make_ifom_proposal()

    records = asyncio.run(service.apply_proposals([proposal], current_values={
        "active:coding": 3600.0
    }))
    assert records == []
    # No override written
    assert ttl_store.read_override("sess-1", "coding", "active") is None


# ---------------------------------------------------------------------------
# No ifom_mutator → blocked
# ---------------------------------------------------------------------------


def test_no_ifom_mutator_blocks_proposal():
    weight_store = InMemorySynapseWeightStore()
    synapse_mutator = SynapseWeightMutator(store=weight_store)
    service = RPEMutationService(
        mutator=synapse_mutator,
        logger=get_spinal_logger(),
        config=ActiveMutationConfig(active_enabled=True),
        ifom_mutator=None,  # explicitly None
    )
    proposal = _make_ifom_proposal()
    records = asyncio.run(service.apply_proposals([proposal], current_values={
        "active:coding": 3600.0
    }))
    assert records == []


# ---------------------------------------------------------------------------
# Successful IFOM TTL mutation
# ---------------------------------------------------------------------------


def test_apply_ifom_ttl_proposal_succeeds():
    service, ttl_store, _ = _make_service(enabled=True)
    proposal = _make_ifom_proposal(proposed_delta=300.0)
    records = asyncio.run(service.apply_proposals(
        [proposal],
        current_values={"active:coding": 3600.0},
    ))
    assert len(records) == 1
    record = records[0]
    assert record.proposal.target == "ifom_ttl"
    assert abs(record.previous_value - 3600.0) < 1e-6
    assert abs(record.new_value - 3900.0) < 1e-6


def test_apply_ifom_ttl_writes_to_store():
    service, ttl_store, _ = _make_service(enabled=True)
    proposal = _make_ifom_proposal(proposed_delta=300.0)
    asyncio.run(service.apply_proposals(
        [proposal],
        current_values={"active:coding": 3600.0},
    ))
    override = ttl_store.read_override("sess-1", "coding", "active")
    assert override is not None
    assert abs(override.override_seconds - 3900.0) < 1e-6


def test_apply_ifom_ttl_reads_existing_override():
    """If an override already exists in store, use it as previous_value."""
    service, ttl_store, _ = _make_service(enabled=True)
    ttl_store.set("sess-1", "coding", "active", override_seconds=4500.0)
    proposal = _make_ifom_proposal(proposed_delta=120.0)
    records = asyncio.run(service.apply_proposals([proposal]))
    assert len(records) == 1
    # previous_value should be the store value (4500), not current_values hint
    assert abs(records[0].previous_value - 4500.0) < 1e-6
    assert abs(records[0].new_value - 4620.0) < 1e-6


def test_apply_ifom_ttl_fallback_to_current_values():
    """When no override in store, use current_values hint."""
    service, ttl_store, _ = _make_service(enabled=True)
    proposal = _make_ifom_proposal(proposed_delta=120.0)
    records = asyncio.run(service.apply_proposals(
        [proposal],
        current_values={"active:coding": 3600.0},
    ))
    assert len(records) == 1
    assert abs(records[0].previous_value - 3600.0) < 1e-6


def test_apply_ifom_ttl_no_value_returns_empty():
    """No override in store AND no current_values → nothing applied."""
    service, ttl_store, _ = _make_service(enabled=True)
    proposal = _make_ifom_proposal()
    records = asyncio.run(service.apply_proposals([proposal], current_values={}))
    assert records == []


# ---------------------------------------------------------------------------
# Threshold blocking
# ---------------------------------------------------------------------------


def test_low_confidence_blocks_ifom_ttl():
    service, _, _ = _make_service(enabled=True)
    # confidence=0.3 < min_confidence=0.5
    decision = _make_decision(confidence=0.3)
    proposal = _make_ifom_proposal(decision=decision)
    records = asyncio.run(service.apply_proposals(
        [proposal], current_values={"active:coding": 3600.0}
    ))
    assert records == []


def test_low_pe_blocks_ifom_ttl():
    service, _, _ = _make_service(enabled=True)
    # pe = 0.6 - 0.5 = 0.1 < min_abs_prediction_error=0.3
    decision = _make_decision(expected_reward=0.5, actual_reward=0.6, confidence=0.8)
    proposal = _make_ifom_proposal(decision=decision, proposed_delta=8.0)  # small
    records = asyncio.run(service.apply_proposals(
        [proposal], current_values={"active:coding": 3600.0}
    ))
    assert records == []


# ---------------------------------------------------------------------------
# Per-trace-target single-apply
# ---------------------------------------------------------------------------


def test_single_apply_per_trace_target_ifom_ttl():
    """Two proposals with same (trace_id, target_key) → only 1 applied."""
    service, ttl_store, _ = _make_service(enabled=True)
    decision = _make_decision(trace_id="tr-same")
    p1 = _make_ifom_proposal(decision=decision, proposed_delta=120.0)
    p2 = _make_ifom_proposal(decision=decision, proposed_delta=60.0)
    records = asyncio.run(service.apply_proposals(
        [p1, p2], current_values={"active:coding": 3600.0}
    ))
    assert len(records) == 1


# ---------------------------------------------------------------------------
# Mixed synapse_weight + ifom_ttl in same call
# ---------------------------------------------------------------------------


def test_mixed_targets_both_applied():
    weight_store = InMemorySynapseWeightStore()
    weight_store.set("sess-1", "coding", 0.5)
    synapse_mutator = SynapseWeightMutator(store=weight_store)
    ttl_store = InMemoryIFOMTTLOverrideStore()
    ifom_mutator = IFOMTTLMutator(store=ttl_store)
    service = RPEMutationService(
        mutator=synapse_mutator,
        logger=get_spinal_logger(),
        config=ActiveMutationConfig(active_enabled=True),
        ifom_mutator=ifom_mutator,
    )

    decision = _make_decision(trace_id="tr-mix", expected_reward=0.1, actual_reward=0.9, confidence=0.8)
    sw_proposal = RPEProposal(
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
    ifom_proposal = _make_ifom_proposal(decision=decision, proposed_delta=120.0)

    records = asyncio.run(service.apply_proposals(
        [sw_proposal, ifom_proposal],
        current_values={"category:coding": 0.5, "active:coding": 3600.0},
    ))
    # Both should succeed (different target_keys)
    targets = {r.proposal.target for r in records}
    assert "synapse_weight" in targets
    assert "ifom_ttl" in targets

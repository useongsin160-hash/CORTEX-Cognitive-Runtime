"""Phase 6 STEP 3.1 — SynapseWeightMutator + stores tests."""

from __future__ import annotations

import uuid

import pytest

from app.rpe.models import RPEContext, RPEDecision, RPEProposal, RPEReward
from app.rpe.mutators import (
    InMemorySynapseWeightStore,
    SynapseWeightMutator,
    SynapseWeightStoreProtocol,
    parse_category_from_target_key,
)


def _proposal(
    *,
    category: str = "coding",
    session_id: str = "sess-1",
    proposed_delta: float = 0.05,
    max_delta: float = 0.1,
    confidence: float = 0.5,
    actual_reward: float = 0.85,
) -> RPEProposal:
    reward = RPEReward(
        source="mock",
        expected_reward=0.5,
        actual_reward=actual_reward,
        confidence=confidence,
    )
    context = RPEContext(
        trace_id="trace-mut",
        session_id=session_id,
        category=category,
    )
    decision = RPEDecision(reward=reward, context=context)
    return RPEProposal(
        decision=decision,
        target="synapse_weight",
        target_key=f"category:{category}",
        current_value=0.5,
        proposed_delta=proposed_delta,
        proposed_value=min(max(0.5 + proposed_delta, 0.1), 1.0),
        max_delta=max_delta,
        rollback_id=str(uuid.uuid4()),
        confidence=confidence,
        applied=False,
    )


class TestParseCategory:
    def test_valid(self) -> None:
        assert parse_category_from_target_key("category:coding") == "coding"

    def test_with_underscore(self) -> None:
        assert parse_category_from_target_key("category:game_design") == "game_design"

    def test_invalid_prefix(self) -> None:
        with pytest.raises(ValueError, match="category:"):
            parse_category_from_target_key("synapse:coding")


class TestInMemoryStore:
    def test_protocol_implementation(self) -> None:
        store = InMemorySynapseWeightStore()
        assert isinstance(store, SynapseWeightStoreProtocol)

    @pytest.mark.asyncio
    async def test_read_missing_returns_none(self) -> None:
        store = InMemorySynapseWeightStore()
        assert await store.read_weight("sess-1", "coding") is None

    @pytest.mark.asyncio
    async def test_write_then_read(self) -> None:
        store = InMemorySynapseWeightStore()
        await store.write_weight("sess-1", "coding", 0.45)
        assert await store.read_weight("sess-1", "coding") == 0.45

    @pytest.mark.asyncio
    async def test_session_isolation(self) -> None:
        store = InMemorySynapseWeightStore()
        await store.write_weight("sess-1", "coding", 0.45)
        await store.write_weight("sess-2", "coding", 0.85)
        assert await store.read_weight("sess-1", "coding") == 0.45
        assert await store.read_weight("sess-2", "coding") == 0.85

    @pytest.mark.asyncio
    async def test_initial_seeded(self) -> None:
        store = InMemorySynapseWeightStore(initial={("s", "writing"): 0.7})
        assert await store.read_weight("s", "writing") == 0.7


class TestMutatorConstruction:
    def test_default_bounds(self) -> None:
        m = SynapseWeightMutator(store=InMemorySynapseWeightStore())
        assert m._weight_min == 0.1  # type: ignore[attr-defined]
        assert m._weight_max == 1.0  # type: ignore[attr-defined]

    def test_invalid_min(self) -> None:
        with pytest.raises(ValueError):
            SynapseWeightMutator(store=InMemorySynapseWeightStore(), weight_min=-0.1)

    def test_invalid_max(self) -> None:
        with pytest.raises(ValueError):
            SynapseWeightMutator(store=InMemorySynapseWeightStore(), weight_max=1.5)

    def test_min_ge_max(self) -> None:
        with pytest.raises(ValueError):
            SynapseWeightMutator(
                store=InMemorySynapseWeightStore(),
                weight_min=0.5,
                weight_max=0.5,
            )


class TestReadCurrentWeight:
    @pytest.mark.asyncio
    async def test_reads_via_target_key(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.42})
        m = SynapseWeightMutator(store=store)
        assert await m.read_current_weight("sess-1", "category:coding") == 0.42

    @pytest.mark.asyncio
    async def test_missing_returns_none(self) -> None:
        store = InMemorySynapseWeightStore()
        m = SynapseWeightMutator(store=store)
        assert await m.read_current_weight("sess-1", "category:writing") is None


class TestApplyMutation:
    @pytest.mark.asyncio
    async def test_applies_delta(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
        m = SynapseWeightMutator(store=store)
        proposal = _proposal(proposed_delta=0.05)
        record = await m.apply_mutation(
            proposal=proposal,
            previous_value=0.5,
            lock_key="synapse_weight:category:coding",
        )
        assert record.previous_value == 0.5
        assert record.applied_delta == pytest.approx(0.05)
        assert record.new_value == pytest.approx(0.55)
        # store updated
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_upper_clamp(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.99})
        m = SynapseWeightMutator(store=store)
        # max signal allowed: pe=1.0*conf=1.0*max=0.1 → delta=0.1 (proposal)
        reward = RPEReward(source="mock", expected_reward=0.0, actual_reward=1.0, confidence=1.0)
        ctx = RPEContext(trace_id="t", session_id="sess-1", category="coding")
        d = RPEDecision(reward=reward, context=ctx)
        proposal = RPEProposal(
            decision=d,
            target="synapse_weight",
            target_key="category:coding",
            current_value=0.99,
            proposed_delta=0.1,
            proposed_value=1.0,
            max_delta=0.1,
            rollback_id=str(uuid.uuid4()),
            confidence=1.0,
            applied=False,
        )
        record = await m.apply_mutation(
            proposal=proposal,
            previous_value=0.99,
            lock_key="synapse_weight:category:coding",
        )
        assert record.new_value == pytest.approx(1.0)
        # applied_delta clamped to 0.01, within max_delta=0.1
        assert record.applied_delta == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_lower_clamp(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.12})
        m = SynapseWeightMutator(store=store)
        reward = RPEReward(source="mock", expected_reward=0.75, actual_reward=0.25, confidence=0.5)
        ctx = RPEContext(trace_id="t", session_id="sess-1", category="coding")
        d = RPEDecision(reward=reward, context=ctx)
        proposal = RPEProposal(
            decision=d,
            target="synapse_weight",
            target_key="category:coding",
            current_value=0.12,
            proposed_delta=-0.025,
            proposed_value=0.1,
            max_delta=0.1,
            rollback_id=str(uuid.uuid4()),
            confidence=0.5,
            applied=False,
        )
        record = await m.apply_mutation(
            proposal=proposal,
            previous_value=0.12,
            lock_key="synapse_weight:category:coding",
        )
        assert record.new_value == pytest.approx(0.1)
        assert record.applied_delta == pytest.approx(-0.02)

    @pytest.mark.asyncio
    async def test_previous_out_of_bounds_raises(self) -> None:
        store = InMemorySynapseWeightStore()
        m = SynapseWeightMutator(store=store)
        proposal = _proposal()
        with pytest.raises(ValueError):
            await m.apply_mutation(
                proposal=proposal,
                previous_value=0.05,
                lock_key="synapse_weight:category:coding",
            )

    @pytest.mark.asyncio
    async def test_session_id_none_raises(self) -> None:
        store = InMemorySynapseWeightStore()
        m = SynapseWeightMutator(store=store)
        reward = RPEReward(source="mock", expected_reward=0.5, actual_reward=0.8, confidence=0.5)
        # session_id None
        ctx = RPEContext(trace_id="t", session_id=None, category="coding")
        d = RPEDecision(reward=reward, context=ctx)
        proposal = RPEProposal(
            decision=d,
            target="synapse_weight",
            target_key="category:coding",
            current_value=0.5,
            proposed_delta=0.05,
            proposed_value=0.55,
            max_delta=0.1,
            rollback_id=str(uuid.uuid4()),
            confidence=0.5,
            applied=False,
        )
        with pytest.raises(ValueError, match="session_id"):
            await m.apply_mutation(
                proposal=proposal,
                previous_value=0.5,
                lock_key="synapse_weight:category:coding",
            )


class TestRollback:
    @pytest.mark.asyncio
    async def test_restores_previous_value(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
        m = SynapseWeightMutator(store=store)
        proposal = _proposal(proposed_delta=0.05)
        rec = await m.apply_mutation(
            proposal=proposal,
            previous_value=0.5,
            lock_key="synapse_weight:category:coding",
        )
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.55)
        rolled = await m.rollback(rec)
        assert rolled.rollback_status == "rolled_back"
        assert rolled.new_value == pytest.approx(0.5)
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_rollback_returns_new_frozen_record(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
        m = SynapseWeightMutator(store=store)
        proposal = _proposal()
        rec = await m.apply_mutation(
            proposal=proposal,
            previous_value=0.5,
            lock_key="synapse_weight:category:coding",
        )
        rolled = await m.rollback(rec)
        # Original record must not be mutated.
        assert rec.rollback_status == "available"
        assert rolled is not rec
        assert rolled.rollback_id == rec.rollback_id

    @pytest.mark.asyncio
    async def test_rollback_preserves_rollback_id(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
        m = SynapseWeightMutator(store=store)
        proposal = _proposal()
        rec = await m.apply_mutation(
            proposal=proposal,
            previous_value=0.5,
            lock_key="synapse_weight:category:coding",
        )
        rolled = await m.rollback(rec)
        assert rolled.rollback_id == proposal.rollback_id

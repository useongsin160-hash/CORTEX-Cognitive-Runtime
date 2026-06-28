"""RPE active mutation: synapse weight mutator + IFOM TTL mutator.

Phase 6 STEP 3.1: SynapseWeightMutator (async, wraps SynapseStore).
Phase 6 STEP 4:   IFOMTTLMutator (sync, wraps IFOMTTLOverrideStore).

Protocol-based design:
- ``SynapseWeightStoreProtocol``: minimal read/write interface.
- ``InMemorySynapseWeightStore``: test-only deterministic backend.
- ``SynapseStoreAdapter``: production adapter wrapping app.synapse.store.SynapseStore.
- ``IFOMTTLMutator``: sync mutator for IFOMTTLOverrideStore.

STEP 3.1 isolation rule: app.synapse import is permitted here (this is the
single entry point for store integration). app.api.routes, app.execution.swarm,
app.main, app.memory, app.routing remain forbidden.

STEP 4 isolation rule: app.rpe.ifom_store is permitted here.
Global IFOMConfig is NEVER mutated. Only session-scoped overrides are written.

No automatic rollback scheduler. Manual rollback only.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from app.rpe.difficulty_store import (
    SynapseDifficultyWeightStoreProtocol,
    parse_cat_diff_target_key,
)
from app.rpe.ifom_store import (
    IFOMTTLOverride,
    IFOMTTLOverrideStoreProtocol,
    IFOMTTLType,
    parse_ifom_ttl_target_key,
)
from app.rpe.models import RPEMutationRecord, RPEProposal, _clamp


def parse_category_from_target_key(target_key: str) -> str:
    """Extract category name from target_key (``category:{name}``)."""
    prefix = "category:"
    if not target_key.startswith(prefix):
        raise ValueError(
            f"target_key must start with {prefix!r}, got {target_key!r}"
        )
    return target_key[len(prefix):]


@runtime_checkable
class SynapseWeightStoreProtocol(Protocol):
    async def read_weight(
        self, session_id: str, category: str
    ) -> float | None: ...

    async def write_weight(
        self, session_id: str, category: str, value: float
    ) -> None: ...


class InMemorySynapseWeightStore:
    """Deterministic in-memory store for STEP 3.1 unit tests.

    Keyed by (session_id, category). Missing keys read as None.
    """

    def __init__(
        self,
        initial: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._weights: dict[tuple[str, str], float] = dict(initial or {})

    async def read_weight(self, session_id: str, category: str) -> float | None:
        return self._weights.get((session_id, category))

    async def write_weight(
        self, session_id: str, category: str, value: float
    ) -> None:
        self._weights[(session_id, category)] = value

    # ----- test helpers -----

    def snapshot(self) -> dict[tuple[str, str], float]:
        return dict(self._weights)

    def set(self, session_id: str, category: str, value: float) -> None:
        self._weights[(session_id, category)] = value


class SynapseStoreAdapter:
    """Adapter over app.synapse.store.SynapseStore.

    Production wrapper that exposes the minimal read/write interface
    over the existing SynapseState.weights dict.
    """

    def __init__(self, store: object) -> None:
        # `store` is app.synapse.store.SynapseStore but typed as object
        # to keep this adapter import-light in unit tests.
        self._store = store

    async def read_weight(self, session_id: str, category: str) -> float | None:
        state = await self._store.get_state(session_id)  # type: ignore[attr-defined]
        weights = getattr(state, "weights", None)
        if not isinstance(weights, dict):
            return None
        value = weights.get(category)
        return float(value) if value is not None else None

    async def write_weight(
        self, session_id: str, category: str, value: float
    ) -> None:
        state = await self._store.get_state(session_id)  # type: ignore[attr-defined]
        state.weights[category] = float(value)
        await self._store.update_state(session_id, state)  # type: ignore[attr-defined]


class SynapseWeightMutator:
    """Encapsulate weight reads, writes, and rollbacks for a single proposal.

    Locking is the orchestrator's responsibility (RPEMutationService).
    This class does NOT acquire locks.
    """

    def __init__(
        self,
        store: SynapseWeightStoreProtocol,
        weight_min: float = 0.1,
        weight_max: float = 1.0,
    ) -> None:
        if weight_min < 0:
            raise ValueError(f"weight_min must be >= 0, got {weight_min}")
        if weight_max > 1.0:
            raise ValueError(f"weight_max must be <= 1.0, got {weight_max}")
        if weight_min >= weight_max:
            raise ValueError(
                f"weight_min ({weight_min}) must be < weight_max ({weight_max})"
            )
        self._store = store
        self._weight_min = weight_min
        self._weight_max = weight_max

    async def read_current_weight(
        self, session_id: str, target_key: str
    ) -> float | None:
        category = parse_category_from_target_key(target_key)
        return await self._store.read_weight(session_id, category)

    async def apply_mutation(
        self,
        proposal: RPEProposal,
        previous_value: float,
        lock_key: str,
        current_value_mismatch: bool = False,
        expires_at: float | None = None,
    ) -> RPEMutationRecord:
        """Apply proposal to store. Returns immutable record.

        applied_delta is recomputed against the *actual* previous_value
        (which may differ from proposal.current_value due to staleness),
        and clamped via proposed_value clamp.
        """
        if not (self._weight_min <= previous_value <= self._weight_max):
            raise ValueError(
                f"previous_value {previous_value} outside "
                f"[{self._weight_min}, {self._weight_max}]"
            )

        category = parse_category_from_target_key(proposal.target_key)
        target_value = previous_value + proposal.proposed_delta
        new_value = _clamp(target_value, self._weight_min, self._weight_max)
        applied_delta = new_value - previous_value

        session_id = proposal.decision.context.session_id
        if session_id is None:
            raise ValueError(
                "RPEContext.session_id must not be None for active mutation"
            )

        await self._store.write_weight(session_id, category, new_value)

        return RPEMutationRecord(
            proposal=proposal,
            previous_value=previous_value,
            applied_delta=applied_delta,
            new_value=new_value,
            applied_at=time.monotonic(),
            rollback_id=proposal.rollback_id,
            lock_key=lock_key,
            expires_at=expires_at,
            rollback_status="available",
            weight_min=self._weight_min,
            weight_max=self._weight_max,
            current_value_mismatch=current_value_mismatch,
        )

    async def rollback(self, record: RPEMutationRecord) -> RPEMutationRecord:
        """Restore previous_value. Returns new record with status=rolled_back.

        Idempotency: the orchestrator must check record.rollback_status
        before calling this method. If called on a non-available record,
        this method still performs the write — callers should guard.
        """
        category = parse_category_from_target_key(record.proposal.target_key)
        session_id = record.proposal.decision.context.session_id
        if session_id is None:
            raise ValueError(
                "session_id must not be None during rollback"
            )

        await self._store.write_weight(session_id, category, record.previous_value)

        # Build a rolled-back record. The new record represents the rollback
        # itself: applied_delta is the reverse of the original mutation
        # (current_value_before_rollback → previous_value).
        reverse_delta = record.previous_value - record.new_value
        return RPEMutationRecord(
            proposal=record.proposal,
            previous_value=record.new_value,  # state right before rollback
            applied_delta=reverse_delta,
            new_value=record.previous_value,  # restored value
            applied_at=time.monotonic(),
            rollback_id=record.rollback_id,
            lock_key=record.lock_key,
            expires_at=record.expires_at,
            rollback_status="rolled_back",
            weight_min=record.weight_min,
            weight_max=record.weight_max,
            current_value_mismatch=record.current_value_mismatch,
        )


# ---------------------------------------------------------------------------
# B11 S1: category×difficulty mutator (35-cell isolated store)
# ---------------------------------------------------------------------------


class SynapseDifficultyWeightMutator:
    """Reads/writes/rolls back weights in the 35-cell (category×difficulty) store.

    Mirrors SynapseWeightMutator but addresses the difficulty store via
    (category, difficulty) parsed from a ``category:{cat}:difficulty:{d}`` key.
    Additive — does NOT replace SynapseWeightMutator; the production 7-cell path
    stays untouched/frozen. Locking is the orchestrator's responsibility.

    clamp bounds [0.1, 1.0] are inherited; the 1.0 ceiling is never raised
    (emergent invariant — no per-difficulty cap).
    """

    def __init__(
        self,
        store: SynapseDifficultyWeightStoreProtocol,
        weight_min: float = 0.1,
        weight_max: float = 1.0,
        seed_weight: float = 0.3,
    ) -> None:
        if weight_min < 0:
            raise ValueError(f"weight_min must be >= 0, got {weight_min}")
        if weight_max > 1.0:
            raise ValueError(f"weight_max must be <= 1.0, got {weight_max}")
        if weight_min >= weight_max:
            raise ValueError(
                f"weight_min ({weight_min}) must be < weight_max ({weight_max})"
            )
        if not (weight_min <= seed_weight <= weight_max):
            raise ValueError(
                f"seed_weight {seed_weight} outside [{weight_min}, {weight_max}]"
            )
        self._store = store
        self._weight_min = weight_min
        self._weight_max = weight_max
        self._seed_weight = seed_weight

    async def read_current_weight(
        self, session_id: str, target_key: str
    ) -> float | None:
        """Return the stored cell weight, or the seed for an unlearned cell.

        The seed lets the dedicated learning service apply to a never-written
        cell (the store itself still returns None for the gate, so an unlearned
        cell stays a no-op overlay until a mutation actually writes it).
        """
        category, difficulty = parse_cat_diff_target_key(target_key)
        value = await self._store.read_weight(session_id, category, difficulty)
        return value if value is not None else self._seed_weight

    async def apply_mutation(
        self,
        proposal: RPEProposal,
        previous_value: float,
        lock_key: str,
        current_value_mismatch: bool = False,
        expires_at: float | None = None,
    ) -> RPEMutationRecord:
        """Apply proposal to the 35-cell store. Returns an immutable record.

        applied_delta is recomputed against the *actual* previous_value and
        clamped to [weight_min, weight_max] (same shape as SynapseWeightMutator).
        """
        if not (self._weight_min <= previous_value <= self._weight_max):
            raise ValueError(
                f"previous_value {previous_value} outside "
                f"[{self._weight_min}, {self._weight_max}]"
            )

        category, difficulty = parse_cat_diff_target_key(proposal.target_key)
        target_value = previous_value + proposal.proposed_delta
        new_value = _clamp(target_value, self._weight_min, self._weight_max)
        applied_delta = new_value - previous_value

        session_id = proposal.decision.context.session_id
        if session_id is None:
            raise ValueError(
                "RPEContext.session_id must not be None for active mutation"
            )

        await self._store.write_weight(session_id, category, difficulty, new_value)

        return RPEMutationRecord(
            proposal=proposal,
            previous_value=previous_value,
            applied_delta=applied_delta,
            new_value=new_value,
            applied_at=time.monotonic(),
            rollback_id=proposal.rollback_id,
            lock_key=lock_key,
            expires_at=expires_at,
            rollback_status="available",
            weight_min=self._weight_min,
            weight_max=self._weight_max,
            current_value_mismatch=current_value_mismatch,
        )

    async def rollback(self, record: RPEMutationRecord) -> RPEMutationRecord:
        """Restore previous_value in the 35-cell store. Returns rolled_back record."""
        category, difficulty = parse_cat_diff_target_key(record.proposal.target_key)
        session_id = record.proposal.decision.context.session_id
        if session_id is None:
            raise ValueError("session_id must not be None during rollback")

        await self._store.write_weight(
            session_id, category, difficulty, record.previous_value
        )

        reverse_delta = record.previous_value - record.new_value
        return RPEMutationRecord(
            proposal=record.proposal,
            previous_value=record.new_value,
            applied_delta=reverse_delta,
            new_value=record.previous_value,
            applied_at=time.monotonic(),
            rollback_id=record.rollback_id,
            lock_key=record.lock_key,
            expires_at=record.expires_at,
            rollback_status="rolled_back",
            weight_min=record.weight_min,
            weight_max=record.weight_max,
            current_value_mismatch=record.current_value_mismatch,
        )


# ---------------------------------------------------------------------------
# STEP 4: IFOM TTL mutator (sync)
# ---------------------------------------------------------------------------


class IFOMTTLMutator:
    """Sync mutator for IFOM TTL session-scoped overrides.

    Phase 6 STEP 4.

    Sync because IFOMPolicy.adjust_ttl_with_rpe_hook is sync.
    Reads/writes IFOMTTLOverrideStoreProtocol (all O(1) for in-memory backend).

    Global IFOMConfig is NEVER mutated.  Only per-(session, category, ttl_type)
    overrides are written to the store.

    Locking is the orchestrator's responsibility (RPEMutationService).
    This class does NOT acquire locks.
    """

    def __init__(
        self,
        store: IFOMTTLOverrideStoreProtocol,
        ttl_min: float = 60.0,
        ttl_max: float = 86400.0,
    ) -> None:
        if ttl_min <= 0:
            raise ValueError(f"ttl_min must be > 0, got {ttl_min}")
        if ttl_max <= ttl_min:
            raise ValueError(
                f"ttl_max ({ttl_max}) must be > ttl_min ({ttl_min})"
            )
        self._store = store
        self._ttl_min = ttl_min
        self._ttl_max = ttl_max

    def read_current_ttl(
        self, session_id: str, target_key: str
    ) -> float | None:
        """Read the current override TTL for (session_id, ttl_type, category).

        Returns None if no override exists (no entry in store).
        The caller decides how to handle a None result.
        """
        ttl_type, category = parse_ifom_ttl_target_key(target_key)
        override = self._store.read_override(session_id, category, ttl_type)
        return override.override_seconds if override is not None else None

    def apply_mutation(
        self,
        proposal: RPEProposal,
        previous_value: float,
        lock_key: str,
        current_value_mismatch: bool = False,
        expires_at: float | None = None,
    ) -> RPEMutationRecord:
        """Apply proposal to store. Returns immutable record.

        applied_delta is recomputed against the *actual* previous_value
        (which may differ from proposal.current_value due to staleness),
        and the result is clamped to [ttl_min, ttl_max].

        Raises ValueError if session_id is None or previous_value out of bounds.
        """
        if not (self._ttl_min <= previous_value <= self._ttl_max):
            raise ValueError(
                f"previous_value {previous_value} outside "
                f"[{self._ttl_min}, {self._ttl_max}]"
            )

        ttl_type, category = parse_ifom_ttl_target_key(proposal.target_key)

        target_value = previous_value + proposal.proposed_delta
        new_value = _clamp(target_value, self._ttl_min, self._ttl_max)
        applied_delta = new_value - previous_value

        session_id = proposal.decision.context.session_id
        if session_id is None:
            raise ValueError(
                "RPEContext.session_id must not be None for active IFOM TTL mutation"
            )

        override = IFOMTTLOverride(
            session_id=session_id,
            category=category,
            ttl_type=ttl_type,
            override_seconds=new_value,
            applied_at=time.monotonic(),
            rollback_id=proposal.rollback_id,
            rollback_status="available",
            previous_seconds=previous_value,
        )
        self._store.write_override(override)

        return RPEMutationRecord(
            proposal=proposal,
            previous_value=previous_value,
            applied_delta=applied_delta,
            new_value=new_value,
            applied_at=override.applied_at,
            rollback_id=proposal.rollback_id,
            lock_key=lock_key,
            expires_at=expires_at,
            rollback_status="available",
            weight_min=self._ttl_min,
            weight_max=self._ttl_max,
            current_value_mismatch=current_value_mismatch,
        )

    def rollback(self, record: RPEMutationRecord) -> RPEMutationRecord:
        """Restore previous override (or delete if previous was None).

        Returns new record with rollback_status="rolled_back".
        """
        ttl_type, category = parse_ifom_ttl_target_key(record.proposal.target_key)
        session_id = record.proposal.decision.context.session_id
        if session_id is None:
            raise ValueError("session_id must not be None during IFOM TTL rollback")

        # Determine what to restore: if previous_value was outside bounds,
        # it was the IFOMConfig default (not an override). Delete the override.
        # Otherwise write back the previous override value.
        if record.previous_value <= 0:
            # Previous state: no override existed. Delete the override.
            self._store.delete_override(session_id, category, ttl_type)
        else:
            # Restore previous override.
            restored_override = IFOMTTLOverride(
                session_id=session_id,
                category=category,
                ttl_type=ttl_type,
                override_seconds=record.previous_value,
                applied_at=time.monotonic(),
                rollback_id=record.rollback_id,
                rollback_status="rolled_back",
                previous_seconds=record.new_value,
            )
            self._store.write_override(restored_override)

        reverse_delta = record.previous_value - record.new_value
        return RPEMutationRecord(
            proposal=record.proposal,
            previous_value=record.new_value,
            applied_delta=reverse_delta,
            new_value=record.previous_value,
            applied_at=time.monotonic(),
            rollback_id=record.rollback_id,
            lock_key=record.lock_key,
            expires_at=record.expires_at,
            rollback_status="rolled_back",
            weight_min=record.weight_min,
            weight_max=record.weight_max,
            current_value_mismatch=record.current_value_mismatch,
        )

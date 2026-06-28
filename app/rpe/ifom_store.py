"""IFOM TTL override store.

Phase 6 STEP 4.

IFOMTTLType: 4-value literal matching IFOMConfig fields.
IFOMTTLOverride: frozen dataclass per (session_id, category, ttl_type).
IFOMTTLOverrideStoreProtocol: sync Protocol (must stay sync — IFOMPolicy is sync).
InMemoryIFOMTTLOverrideStore: test-only deterministic backend.

Isolation rules:
- No import from app.memory (IFOMPolicy sees this via resolver Callable only).
- No import from app.rpe.service, app.rpe.dopamine, app.rpe.mutators.
- No import from app.synapse, app.api, app.execution, app.main, app.routing.
- app.rpe.models is allowed (for RPEMutationRecord references if needed).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

IFOMTTLType = Literal["active", "paused", "completed", "low_priority"]

_VALID_TTL_TYPES: frozenset[str] = frozenset(
    {"active", "paused", "completed", "low_priority"}
)


# ---------------------------------------------------------------------------
# Target key helpers
# ---------------------------------------------------------------------------


def build_ifom_ttl_target_key(ttl_type: IFOMTTLType, category: str | None) -> str:
    """Build target_key for IFOM TTL proposal.

    Format: ``{ttl_type}:{category}`` or ``{ttl_type}:`` when category is None.

    Examples:
        build_ifom_ttl_target_key("active", "coding") → "active:coding"
        build_ifom_ttl_target_key("paused", None)     → "paused:"
    """
    cat = category or ""
    return f"{ttl_type}:{cat}"


def parse_ifom_ttl_target_key(target_key: str) -> tuple[IFOMTTLType, str | None]:
    """Parse target_key back to (ttl_type, category).

    Raises ValueError if format invalid or ttl_type unknown.

    Examples:
        parse_ifom_ttl_target_key("active:coding") → ("active", "coding")
        parse_ifom_ttl_target_key("paused:")       → ("paused", None)
    """
    if ":" not in target_key:
        raise ValueError(
            f"Invalid ifom_ttl target_key (missing ':'): {target_key!r}"
        )
    ttl_type, cat_part = target_key.split(":", 1)
    if ttl_type not in _VALID_TTL_TYPES:
        raise ValueError(
            f"Unknown IFOMTTLType {ttl_type!r} in target_key {target_key!r}. "
            f"Valid types: {sorted(_VALID_TTL_TYPES)}"
        )
    category: str | None = cat_part if cat_part else None
    return ttl_type, category  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Override model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IFOMTTLOverride:
    """Single IFOM TTL override entry.

    Scoped to (session_id, category, ttl_type). Represents the result of a
    successful IFOM TTL active mutation — the new TTL value for this scope.

    Global IFOMConfig is NEVER mutated. Overrides are scoped per-session.
    """

    session_id: str
    category: str | None
    ttl_type: IFOMTTLType
    override_seconds: float
    applied_at: float
    rollback_id: str
    rollback_status: Literal["available", "rolled_back", "expired"] = "available"
    previous_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.ttl_type not in _VALID_TTL_TYPES:
            raise ValueError(
                f"ttl_type must be one of {sorted(_VALID_TTL_TYPES)}, "
                f"got {self.ttl_type!r}"
            )
        if self.override_seconds <= 0:
            raise ValueError(
                f"override_seconds must be > 0, got {self.override_seconds}"
            )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IFOMTTLOverrideStoreProtocol(Protocol):
    """Sync Protocol for IFOM TTL override store.

    Sync because IFOMPolicy.adjust_ttl_with_rpe_hook is sync.
    All methods complete in O(1) for the in-memory implementation.
    """

    def read_override(
        self,
        session_id: str,
        category: str | None,
        ttl_type: IFOMTTLType,
    ) -> IFOMTTLOverride | None: ...

    def write_override(self, override: IFOMTTLOverride) -> None: ...

    def delete_override(
        self,
        session_id: str,
        category: str | None,
        ttl_type: IFOMTTLType,
    ) -> None: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_StoreKey = tuple[str, str | None, str]


class InMemoryIFOMTTLOverrideStore:
    """Deterministic in-memory override store for STEP 4 unit tests.

    Keyed by (session_id, category, ttl_type). Missing keys read as None.
    All operations are synchronous (O(1) dict access).
    """

    def __init__(
        self,
        initial: dict[_StoreKey, IFOMTTLOverride] | None = None,
    ) -> None:
        self._overrides: dict[_StoreKey, IFOMTTLOverride] = {}
        if initial:
            for k, v in initial.items():
                self._overrides[k] = v

    def read_override(
        self,
        session_id: str,
        category: str | None,
        ttl_type: IFOMTTLType,
    ) -> IFOMTTLOverride | None:
        return self._overrides.get((session_id, category, ttl_type))

    def write_override(self, override: IFOMTTLOverride) -> None:
        key: _StoreKey = (override.session_id, override.category, override.ttl_type)
        self._overrides[key] = override

    def delete_override(
        self,
        session_id: str,
        category: str | None,
        ttl_type: IFOMTTLType,
    ) -> None:
        self._overrides.pop((session_id, category, ttl_type), None)

    # ----- test helpers -----

    def snapshot(self) -> dict[_StoreKey, IFOMTTLOverride]:
        """Return a shallow copy of the current store state."""
        return dict(self._overrides)

    def set(
        self,
        session_id: str,
        category: str | None,
        ttl_type: IFOMTTLType,
        override_seconds: float,
        rollback_id: str = "test-rollback-id",
        applied_at: float | None = None,
        previous_seconds: float | None = None,
    ) -> IFOMTTLOverride:
        """Convenience helper: create and store an IFOMTTLOverride."""
        override = IFOMTTLOverride(
            session_id=session_id,
            category=category,
            ttl_type=ttl_type,
            override_seconds=override_seconds,
            applied_at=applied_at if applied_at is not None else time.monotonic(),
            rollback_id=rollback_id,
            previous_seconds=previous_seconds,
        )
        self.write_override(override)
        return override

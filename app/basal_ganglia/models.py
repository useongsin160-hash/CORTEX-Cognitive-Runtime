"""BasalGanglia data model.

Phase 6 STEP 5.1.

Frozen dataclasses for read-only / recommendation-only action selection.

Snapshot-only design:
- ActionSelectionContext stores primitive snapshots, never live objects.
- No dict fields — tuple-of-pairs is used for mappings so the frozen
  dataclass remains hashable and provably immutable.
- ActionSelectionDecision.applied is hard-locked to False.

Isolation rules:
- No imports from app.api, app.execution, app.main, app.routing, app.rpe,
  app.synapse, app.memory, app.core.lock_manager.
- app.core.logging is permitted (advisor only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

JsonScalar = Union[str, int, float, bool, None]

ActionCandidateType = Literal[
    "swarm_full",
    "swarm_minimal",
    "tier_1_5_augment",
    "fallback",
]

_VALID_CANDIDATE_TYPES: frozenset[str] = frozenset(
    {"swarm_full", "swarm_minimal", "tier_1_5_augment", "fallback"}
)


def _validate_scalar_pairs(
    pairs: tuple[tuple[str, JsonScalar], ...],
    field_name: str,
) -> None:
    """Validate a tuple-of-pairs metadata container."""
    if not isinstance(pairs, tuple):
        raise TypeError(
            f"{field_name} must be tuple of (key, value) pairs, "
            f"got {type(pairs).__name__}"
        )
    seen: set[str] = set()
    for item in pairs:
        if not (isinstance(item, tuple) and len(item) == 2):
            raise TypeError(
                f"{field_name} entries must be 2-tuples of (key, value)"
            )
        key, value = item
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"{field_name} key must be non-empty str, got {key!r}"
            )
        if key in seen:
            raise ValueError(f"duplicate {field_name} key: {key!r}")
        seen.add(key)
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise TypeError(
                f"{field_name} value for {key!r} must be JSON scalar, "
                f"got {type(value).__name__}"
            )


def _validate_float_pairs(
    pairs: tuple[tuple[str, float], ...],
    field_name: str,
    *,
    value_min: float | None = None,
    value_max: float | None = None,
    require_positive: bool = False,
) -> None:
    """Validate a tuple-of-pairs of (str, float) — unique keys, bounded values."""
    if not isinstance(pairs, tuple):
        raise TypeError(
            f"{field_name} must be tuple of (key, value) pairs, "
            f"got {type(pairs).__name__}"
        )
    seen: set[str] = set()
    for item in pairs:
        if not (isinstance(item, tuple) and len(item) == 2):
            raise TypeError(
                f"{field_name} entries must be 2-tuples of (key, value)"
            )
        key, value = item
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"{field_name} key must be non-empty str, got {key!r}"
            )
        if key in seen:
            raise ValueError(f"duplicate {field_name} key: {key!r}")
        seen.add(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(
                f"{field_name} value for {key!r} must be float, "
                f"got {type(value).__name__}"
            )
        fvalue = float(value)
        if require_positive and fvalue <= 0:
            raise ValueError(
                f"{field_name} value for {key!r} must be > 0, got {fvalue}"
            )
        if value_min is not None and fvalue < value_min:
            raise ValueError(
                f"{field_name} value for {key!r} {fvalue} < min {value_min}"
            )
        if value_max is not None and fvalue > value_max:
            raise ValueError(
                f"{field_name} value for {key!r} {fvalue} > max {value_max}"
            )


@dataclass(frozen=True)
class ActionCandidate:
    """A single candidate action under consideration.

    Fields are primitive snapshots — no live object references.
    """

    candidate_id: str
    candidate_type: ActionCandidateType
    target_category: str | None
    synapse_weight: float | None = None
    pfc_confidence: float | None = None
    lc_ne_level: float | None = None
    rpe_recent_positive_count: int = 0
    rpe_recent_negative_count: int = 0
    metadata: tuple[tuple[str, JsonScalar], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError(
                f"candidate_id must be non-empty str, got {self.candidate_id!r}"
            )
        if self.candidate_type not in _VALID_CANDIDATE_TYPES:
            raise ValueError(
                f"candidate_type must be one of {sorted(_VALID_CANDIDATE_TYPES)}, "
                f"got {self.candidate_type!r}"
            )
        if self.synapse_weight is not None and not (
            0.0 <= self.synapse_weight <= 1.0
        ):
            raise ValueError(
                f"synapse_weight must be in [0.0, 1.0], got {self.synapse_weight}"
            )
        if self.pfc_confidence is not None and not (
            0.0 <= self.pfc_confidence <= 1.0
        ):
            raise ValueError(
                f"pfc_confidence must be in [0.0, 1.0], got {self.pfc_confidence}"
            )
        if self.lc_ne_level is not None and not (0.0 <= self.lc_ne_level <= 1.0):
            raise ValueError(
                f"lc_ne_level must be in [0.0, 1.0], got {self.lc_ne_level}"
            )
        if self.rpe_recent_positive_count < 0:
            raise ValueError(
                f"rpe_recent_positive_count must be >= 0, "
                f"got {self.rpe_recent_positive_count}"
            )
        if self.rpe_recent_negative_count < 0:
            raise ValueError(
                f"rpe_recent_negative_count must be >= 0, "
                f"got {self.rpe_recent_negative_count}"
            )
        _validate_scalar_pairs(self.metadata, "metadata")


@dataclass(frozen=True)
class ActionSelectionContext:
    """Immutable snapshot of system state at action selection time.

    Tuple-of-pairs is used for mappings to keep this dataclass hashable and
    immune to mutation of the source mapping.
    """

    trace_id: str
    session_id: str | None
    category: str | None
    difficulty: int
    pfc_active: bool
    pfc_cue_type: str | None
    pfc_confidence: float | None
    pfc_intent_category: str | None
    lc_ne_level: float | None
    lc_intent_label: str | None
    synapse_weights: tuple[tuple[str, float], ...] = ()
    ifom_ttl_overrides: tuple[tuple[str, float], ...] = ()
    rpe_recent_positive_count: int = 0
    rpe_recent_negative_count: int = 0
    metadata: tuple[tuple[str, JsonScalar], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.trace_id, str) or not self.trace_id:
            raise ValueError(
                f"trace_id must be non-empty str, got {self.trace_id!r}"
            )
        if self.difficulty < 0:
            raise ValueError(f"difficulty must be >= 0, got {self.difficulty}")
        if self.pfc_confidence is not None and not (
            0.0 <= self.pfc_confidence <= 1.0
        ):
            raise ValueError(
                f"pfc_confidence must be in [0.0, 1.0], got {self.pfc_confidence}"
            )
        if self.lc_ne_level is not None and not (0.0 <= self.lc_ne_level <= 1.0):
            raise ValueError(
                f"lc_ne_level must be in [0.0, 1.0], got {self.lc_ne_level}"
            )
        if self.rpe_recent_positive_count < 0:
            raise ValueError(
                f"rpe_recent_positive_count must be >= 0, "
                f"got {self.rpe_recent_positive_count}"
            )
        if self.rpe_recent_negative_count < 0:
            raise ValueError(
                f"rpe_recent_negative_count must be >= 0, "
                f"got {self.rpe_recent_negative_count}"
            )
        _validate_float_pairs(
            self.synapse_weights,
            "synapse_weights",
            value_min=0.0,
            value_max=1.0,
        )
        _validate_float_pairs(
            self.ifom_ttl_overrides,
            "ifom_ttl_overrides",
            require_positive=True,
        )
        _validate_scalar_pairs(self.metadata, "metadata")


@dataclass(frozen=True)
class ActionSelectionDecision:
    """Result of one BasalGanglia action selection evaluation.

    STEP 5.1 invariants:
        applied is False (read-only / recommendation-only)
        confidence in [0.0, 1.0]
        if selected is not None, selected must be a member of candidates
        reason is a non-empty str
    """

    context: ActionSelectionContext
    candidates: tuple[ActionCandidate, ...]
    selected: ActionCandidate | None
    confidence: float
    reason: str
    applied: bool = False

    def __post_init__(self) -> None:
        if self.applied:
            raise ValueError(
                "STEP 5.1 invariant: ActionSelectionDecision.applied must be False"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError(
                f"reason must be non-empty str, got {self.reason!r}"
            )
        if self.selected is not None and self.selected not in self.candidates:
            raise ValueError(
                "selected must be a member of candidates"
            )
        if not isinstance(self.candidates, tuple):
            raise TypeError(
                f"candidates must be tuple, got {type(self.candidates).__name__}"
            )


@dataclass(frozen=True)
class ActionSelectionPolicyConfig:
    """Compute-demand modulation factors for ActionSelectionPolicy (BG redesign).

    The policy no longer sums per-candidate signal contributions (which were
    common-mode across the default candidates and so could never break a tie —
    only the LC caution bonus differentiated, and it pointed the wrong way for
    high difficulty). Instead it derives a single compute *demand* D in [0, 1] and
    scores each candidate by how closely its compute level matches D (see
    ActionSelectionPolicy).

    D is anchored at the difficulty's B12 routing band (the structural anchor —
    not a tunable here) and then MODULATED by the other REAL signals as signed
    deviations centered on their neutral point, so neutral signals leave D at the
    routing baseline and only genuine deviation shifts it:
        + ne_demand_factor      * ne                      (NE present → escalate)
        + rpe_demand_factor     * (2*neg_frac - 1)        (failures → escalate)
        - synapse_demand_factor * (2*synapse - 1)         (familiar → de-escalate)
        - pfc_demand_factor     * (2*pfc_conf - 1)        (confident → de-escalate)
    A missing signal contributes nothing (its neutral deviation is zero — never a
    fabricated value). Each factor is a deviation magnitude (kept below the ~0.333
    band width so no single signal demotes/promotes a whole band alone); all must
    be >= 0. All four = 0 degenerates to pure difficulty-band routing.
    """

    ne_demand_factor: float = 0.20
    rpe_demand_factor: float = 0.15
    synapse_demand_factor: float = 0.15
    pfc_demand_factor: float = 0.10

    def __post_init__(self) -> None:
        for name in (
            "ne_demand_factor",
            "rpe_demand_factor",
            "synapse_demand_factor",
            "pfc_demand_factor",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(
                    f"{name} must be float, got {type(value).__name__}"
                )
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")

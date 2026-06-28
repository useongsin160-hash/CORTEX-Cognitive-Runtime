"""RPE data model.

RPEContext, RPEReward, RPEDecision are immutable snapshots that flow
through DopamineRPE in observe-only mode (Phase 6 STEP 1).

Phase 6 STEP 2 additions:
- DryRunConfig: dry-run settings (targets, bounds, categories).
- RPEProposal: dry-run result model. Separate from RPEDecision.
  RPEDecision observe-only invariant is preserved.

Phase 6 STEP 3.1 additions:
- ActiveMutationConfig: active mutation gating (disabled-by-default).
- RPEMutationRecord: active mutation result with pre/post weights and
  rollback metadata. Service unit only — no production pipeline.

Phase 6 STEP 3.2 additions:
- RPEPipelineSnapshot: frozen snapshot of pipeline state at RPE measurement
  time. Built by RPEMutationPipelineWrapper after SwarmResult is available.
  to_rpe_context() converts it into RPEContext for DopamineRPE.apply().

Phase 6 STEP 4 additions:
- DryRunConfig: ifom_ttl bounds fields (ifom_ttl_max_delta, ifom_ttl_min_seconds,
  ifom_ttl_max_seconds). Drops synapse_weight-required constraint.
- ActiveMutationConfig: ifom_ttl bounds fields.
- RPEProposal: extended to allow target in {"synapse_weight", "ifom_ttl"}.
- RPEMutationRecord: target-aware lock_key + bounds validation.
"""

from __future__ import annotations

import uuid
from dataclasses import InitVar, dataclass, field
from typing import Literal, Union

RPEMode = Literal["observe_only", "dry_run", "active"]

RPESignalSource = Literal["mock", "heuristic", "cp3", "user_feedback"]

RPETarget = Literal[
    "synapse_weight",
    "ifom_ttl",
    "pfc_timeout",
    "pfc_confidence",
    "tier_1_5_threshold",
    "epinephrine_threshold",
]

JsonScalar = Union[str, int, float, bool, None]


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


@dataclass(frozen=True)
class RPEContext:
    """Immutable snapshot of system state at RPE measurement time."""

    trace_id: str
    session_id: str | None = None
    category: str | None = None
    difficulty: int = 0
    response_source: str = ""
    latency_ms: float = 0.0
    error_occurred: bool = False
    timeout_occurred: bool = False
    continuation_bypass: bool = False
    pfc_active: bool = False
    pfc_cue_type: str | None = None
    pfc_hint_applied: bool = False
    # B13 — observable SUCCESS signals from the SwarmResult tree. The reward
    # source previously saw only failure (error/timeout/fallback); these let it
    # see process quality. All observed facts (not labels), additive + defaulted
    # so existing constructions are unaffected.
    planner_ok: bool = False
    generator_ok: bool = False
    context_ok: bool = False
    clean_finish: bool = False
    context_mean_similarity: float = 0.0
    extra: tuple[tuple[str, JsonScalar], ...] = ()

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {self.latency_ms}")
        if self.difficulty < 0:
            raise ValueError(f"difficulty must be >= 0, got {self.difficulty}")
        if not isinstance(self.extra, tuple):
            raise TypeError(
                f"extra must be tuple of (key, value) pairs, got {type(self.extra).__name__}"
            )
        seen: set[str] = set()
        for item in self.extra:
            if not (isinstance(item, tuple) and len(item) == 2):
                raise TypeError("extra entries must be 2-tuples of (key, value)")
            key, value = item
            if not isinstance(key, str):
                raise TypeError(f"extra key must be str, got {type(key).__name__}")
            if key in seen:
                raise ValueError(f"duplicate extra key: {key!r}")
            seen.add(key)
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise TypeError(
                    f"extra value for {key!r} must be JSON scalar, "
                    f"got {type(value).__name__}"
                )

    def extra_dict(self) -> dict[str, JsonScalar]:
        return dict(self.extra)


@dataclass(frozen=True)
class RPEReward:
    """Reward signal produced by a reward source."""

    source: RPESignalSource
    expected_reward: float
    actual_reward: float
    confidence: float

    def __post_init__(self) -> None:
        for name, val in (
            ("expected_reward", self.expected_reward),
            ("actual_reward", self.actual_reward),
            ("confidence", self.confidence),
        ):
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{name} must be in [0.0, 1.0], got {val}")

    @property
    def prediction_error(self) -> float:
        return _clamp(self.actual_reward - self.expected_reward, -1.0, 1.0)


@dataclass(frozen=True)
class RPEDecision:
    """Observe-only RPE decision.

    STEP 1 invariants:
        mode == "observe_only"
        applied is False
        target is None
        proposed_delta is None
        rollback_id is None

    Safety slots (max_delta, session_scope, target, proposed_delta,
    rollback_id) are declared here but inactive until STEP 2+.
    """

    reward: RPEReward
    context: RPEContext
    mode: RPEMode = "observe_only"
    max_delta: float = 0.1
    rollback_id: str | None = None
    target: RPETarget | None = None
    proposed_delta: float | None = None
    applied: bool = False
    session_scope: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if self.mode != "observe_only":
            raise ValueError(
                f"STEP 1 invariant: mode must be 'observe_only', got {self.mode!r}"
            )
        if self.applied:
            raise ValueError("STEP 1 invariant: applied must be False")
        if self.target is not None:
            raise ValueError(
                f"STEP 1 invariant: target must be None, got {self.target!r}"
            )
        if self.proposed_delta is not None:
            raise ValueError(
                f"STEP 1 invariant: proposed_delta must be None, got {self.proposed_delta!r}"
            )
        if self.rollback_id is not None:
            raise ValueError(
                f"STEP 1 invariant: rollback_id must be None, got {self.rollback_id!r}"
            )
        if self.max_delta < 0:
            raise ValueError(f"max_delta must be >= 0, got {self.max_delta}")
        if self.trace_id is None:
            object.__setattr__(self, "trace_id", self.context.trace_id)
        elif self.trace_id != self.context.trace_id:
            raise ValueError(
                f"trace_id mismatch: decision={self.trace_id!r} "
                f"context={self.context.trace_id!r}"
            )
        if (
            self.session_scope is not None
            and self.context.session_id is not None
            and self.session_scope != self.context.session_id
        ):
            raise ValueError(
                f"session_scope mismatch: decision={self.session_scope!r} "
                f"context={self.context.session_id!r}"
            )


# ---------------------------------------------------------------------------
# STEP 2: Dry-run models
# ---------------------------------------------------------------------------

_KNOWN_TARGETS: frozenset[str] = frozenset(
    {
        "synapse_weight",
        "ifom_ttl",
        "pfc_timeout",
        "pfc_confidence",
        "tier_1_5_threshold",
        "epinephrine_threshold",
    }
)

_CANONICAL_CATEGORIES: tuple[str, ...] = (
    "coding",
    "game_design",
    "math_logic",
    "writing",
    "data_analysis",
    "system_design",
    "general",
)


def _is_valid_uuid4(value: str) -> bool:
    try:
        u = uuid.UUID(value, version=4)
        return str(u) == value
    except (ValueError, AttributeError):
        return False


@dataclass(frozen=True)
class DryRunConfig:
    """Configuration for RPE dry-run simulation.

    STEP 2 scope: synapse_weight only.
    STEP 4 scope: ifom_ttl added (session/category scoped TTL overrides).

    enabled_targets controls which calculators run.  The synapse_weight
    requirement is dropped in STEP 4 — any non-empty subset of known
    targets is valid.
    """

    enabled_targets: tuple[RPETarget, ...] = ("synapse_weight",)
    max_delta: float = 0.1
    require_category: bool = True
    allowed_categories: tuple[str, ...] = _CANONICAL_CATEGORIES
    synapse_weight_min: float = 0.1
    synapse_weight_max: float = 1.0
    # STEP 4: IFOM TTL bounds
    ifom_ttl_max_delta: float = 300.0      # seconds (5 minutes)
    ifom_ttl_min_seconds: float = 60.0     # 1 minute lower bound
    ifom_ttl_max_seconds: float = 86400.0  # 24 hours upper bound

    def __post_init__(self) -> None:
        if not self.enabled_targets:
            raise ValueError("enabled_targets must not be empty")
        for t in self.enabled_targets:
            if t not in _KNOWN_TARGETS:
                raise ValueError(f"unknown RPETarget: {t!r}")
        if self.max_delta <= 0:
            raise ValueError(f"max_delta must be > 0, got {self.max_delta}")
        if self.synapse_weight_min < 0:
            raise ValueError(
                f"synapse_weight_min must be >= 0, got {self.synapse_weight_min}"
            )
        if self.synapse_weight_max > 1.0:
            raise ValueError(
                f"synapse_weight_max must be <= 1.0, got {self.synapse_weight_max}"
            )
        if self.synapse_weight_min >= self.synapse_weight_max:
            raise ValueError(
                f"synapse_weight_min ({self.synapse_weight_min}) must be < "
                f"synapse_weight_max ({self.synapse_weight_max})"
            )
        if not self.allowed_categories:
            raise ValueError("allowed_categories must not be empty")
        # STEP 4: IFOM TTL bounds
        if self.ifom_ttl_max_delta <= 0:
            raise ValueError(
                f"ifom_ttl_max_delta must be > 0, got {self.ifom_ttl_max_delta}"
            )
        if self.ifom_ttl_min_seconds <= 0:
            raise ValueError(
                f"ifom_ttl_min_seconds must be > 0, got {self.ifom_ttl_min_seconds}"
            )
        if self.ifom_ttl_max_seconds <= self.ifom_ttl_min_seconds:
            raise ValueError(
                f"ifom_ttl_max_seconds ({self.ifom_ttl_max_seconds}) must be > "
                f"ifom_ttl_min_seconds ({self.ifom_ttl_min_seconds})"
            )


# STEP 4: active proposal targets (extends STEP 2 synapse_weight-only rule)
_ACTIVE_PROPOSAL_TARGETS: frozenset[str] = frozenset({"synapse_weight", "ifom_ttl"})


@dataclass(frozen=True)
class RPEProposal:
    """Dry-run proposal for a single RPE target.

    This is a NEW model distinct from RPEDecision.
    RPEDecision.mode is still locked to "observe_only" (STEP 1 invariant).

    STEP 2 invariants (still apply):
        applied is False
        abs(proposed_delta) <= max_delta
        max_delta > 0
        rollback_id is a valid uuid4 string
        confidence == decision.reward.confidence
        proposed_value is None iff current_value is None

    STEP 4 extension:
        target in {"synapse_weight", "ifom_ttl"}
        (previously only "synapse_weight" was allowed)
    """

    decision: RPEDecision
    target: RPETarget
    target_key: str
    current_value: float | None
    proposed_delta: float
    proposed_value: float | None
    max_delta: float
    rollback_id: str
    confidence: float
    applied: bool = False

    def __post_init__(self) -> None:
        if self.applied:
            raise ValueError("STEP 2 invariant: applied must be False")
        if self.target not in _ACTIVE_PROPOSAL_TARGETS:
            raise ValueError(
                f"STEP 4 invariant: target must be one of "
                f"{sorted(_ACTIVE_PROPOSAL_TARGETS)}, got {self.target!r}"
            )
        if self.max_delta <= 0:
            raise ValueError(f"max_delta must be > 0, got {self.max_delta}")
        if abs(self.proposed_delta) > self.max_delta + 1e-9:
            raise ValueError(
                f"abs(proposed_delta) {abs(self.proposed_delta)} > "
                f"max_delta {self.max_delta}"
            )
        if not _is_valid_uuid4(self.rollback_id):
            raise ValueError(
                f"rollback_id must be a valid uuid4 string, got {self.rollback_id!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if abs(self.confidence - self.decision.reward.confidence) > 1e-9:
            raise ValueError(
                f"confidence {self.confidence} != decision.reward.confidence "
                f"{self.decision.reward.confidence}"
            )
        # Consistency: proposed_value is None iff current_value is None.
        if self.current_value is None and self.proposed_value is not None:
            raise ValueError(
                "proposed_value must be None when current_value is None"
            )
        if self.current_value is not None and self.proposed_value is None:
            raise ValueError(
                "proposed_value must not be None when current_value is provided"
            )


# ---------------------------------------------------------------------------
# STEP 3.1: Active mutation models
# ---------------------------------------------------------------------------

_VALID_ROLLBACK_STATUSES: frozenset[str] = frozenset(
    {"available", "rolled_back", "expired"}
)

_MUTATION_EPSILON: float = 1e-6


@dataclass(frozen=True)
class ActiveMutationConfig:
    """Configuration for active mutation execution.

    STEP 3.1: disabled-by-default. Production pipeline integration is
    deferred to STEP 3.2 — this STEP only ships the service unit.

    STEP 4 additions:
        ifom_ttl_min_seconds, ifom_ttl_max_seconds: bounds for IFOM TTL
        active mutations. Global IFOMConfig is NEVER mutated — only
        session-scoped overrides in IFOMTTLOverrideStore.

    B5 (observe/active switch split):
        The single `enabled` flag is split into two independent gates so
        observe can run in production without enabling any side effect:
        - observe_enabled gates the RPE observe path only (RPEMutationPipelineWrapper
          spawns the background observe/dry-run/log task). Pure observation —
          zero mutation.
        - active_enabled gates the actual mutation (RPEMutationService.apply_proposals).
          Default False is an absolute safety invariant: mutation never runs
          unless explicitly turned on.
        Back-compat: the legacy `enabled` keyword is accepted as an InitVar and
        maps to observe_enabled ONLY — it can NEVER turn on active_enabled.
        Passing legacy `enabled` together with an explicit observe_enabled=True
        is a contradiction and raises ValueError.
    """

    observe_enabled: bool = False
    active_enabled: bool = False
    # B11: gates the category×difficulty 35-cell learning path (separate store).
    # Independent of active_enabled (which gates the frozen 7-cell production
    # path). Forward-declared in S1; consumed by the S2 gate/pipeline wiring.
    difficulty_learning_enabled: bool = False
    min_confidence: float = 0.5
    min_abs_prediction_error: float = 0.3
    lock_timeout_ms: float = 1000.0
    enable_timeout_metadata: bool = False
    synapse_weight_min: float = 0.1
    synapse_weight_max: float = 1.0
    # STEP 4: IFOM TTL mutation bounds
    ifom_ttl_min_seconds: float = 60.0     # 1 minute lower bound
    ifom_ttl_max_seconds: float = 86400.0  # 24 hours upper bound
    # B5: legacy compat (input-only; declared last so positional order of the
    # real fields is undisturbed). Maps to observe_enabled — never active_enabled.
    enabled: InitVar[bool | None] = None

    def __post_init__(self, enabled: bool | None) -> None:
        # B5 back-compat: legacy `enabled` → observe_enabled ONLY. active_enabled
        # is never derived from it (new explicit opt-in is the only way to mutate).
        if enabled is not None:
            if self.observe_enabled:
                raise ValueError(
                    "ActiveMutationConfig: legacy `enabled` cannot be combined with "
                    "observe_enabled=True (contradiction). Use observe_enabled/"
                    "active_enabled directly."
                )
            object.__setattr__(self, "observe_enabled", bool(enabled))
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0.0, 1.0], got {self.min_confidence}"
            )
        if not 0.0 <= self.min_abs_prediction_error <= 1.0:
            raise ValueError(
                f"min_abs_prediction_error must be in [0.0, 1.0], "
                f"got {self.min_abs_prediction_error}"
            )
        if self.lock_timeout_ms <= 0:
            raise ValueError(
                f"lock_timeout_ms must be > 0, got {self.lock_timeout_ms}"
            )
        if self.synapse_weight_min < 0:
            raise ValueError(
                f"synapse_weight_min must be >= 0, got {self.synapse_weight_min}"
            )
        if self.synapse_weight_max > 1.0:
            raise ValueError(
                f"synapse_weight_max must be <= 1.0, got {self.synapse_weight_max}"
            )
        if self.synapse_weight_min >= self.synapse_weight_max:
            raise ValueError(
                f"synapse_weight_min ({self.synapse_weight_min}) must be < "
                f"synapse_weight_max ({self.synapse_weight_max})"
            )
        # STEP 4: IFOM TTL bounds validation
        if self.ifom_ttl_min_seconds <= 0:
            raise ValueError(
                f"ifom_ttl_min_seconds must be > 0, got {self.ifom_ttl_min_seconds}"
            )
        if self.ifom_ttl_max_seconds <= self.ifom_ttl_min_seconds:
            raise ValueError(
                f"ifom_ttl_max_seconds ({self.ifom_ttl_max_seconds}) must be > "
                f"ifom_ttl_min_seconds ({self.ifom_ttl_min_seconds})"
            )


@dataclass(frozen=True)
class RPEMutationRecord:
    """Active mutation result with pre/post values and rollback metadata.

    STEP 3.1 invariants (synapse_weight):
        rollback_status in {"available", "rolled_back", "expired"}
        abs(applied_delta) <= proposal.max_delta
        rollback_id == proposal.rollback_id
        previous_value, new_value in [weight_min, weight_max]
        (previous_value + applied_delta) ≈ new_value
        lock_key.startswith("synapse_weight:")

    STEP 4 extension (ifom_ttl):
        weight_min / weight_max repurposed as generic value bounds
        (hold TTL bounds in seconds for ifom_ttl target).
        lock_key.startswith("ifom_ttl:") for ifom_ttl target.
        Validation is target-aware: lock_key prefix checked per proposal.target.

    Rollback metadata:
        expires_at and rollback_status track manual rollback only.
        No automatic rollback scheduler.
    """

    proposal: RPEProposal
    previous_value: float
    applied_delta: float
    new_value: float
    applied_at: float
    rollback_id: str
    lock_key: str
    expires_at: float | None = None
    rollback_status: Literal["available", "rolled_back", "expired"] = "available"
    weight_min: float = 0.1
    weight_max: float = 1.0
    current_value_mismatch: bool = False

    def __post_init__(self) -> None:
        if self.rollback_status not in _VALID_ROLLBACK_STATUSES:
            raise ValueError(
                f"rollback_status must be one of {sorted(_VALID_ROLLBACK_STATUSES)}, "
                f"got {self.rollback_status!r}"
            )
        if abs(self.applied_delta) > self.proposal.max_delta + _MUTATION_EPSILON:
            raise ValueError(
                f"abs(applied_delta) {abs(self.applied_delta)} > "
                f"proposal.max_delta {self.proposal.max_delta}"
            )
        if self.rollback_id != self.proposal.rollback_id:
            raise ValueError(
                f"rollback_id {self.rollback_id!r} != "
                f"proposal.rollback_id {self.proposal.rollback_id!r}"
            )
        if not (self.weight_min <= self.previous_value <= self.weight_max):
            raise ValueError(
                f"previous_value {self.previous_value} outside "
                f"[{self.weight_min}, {self.weight_max}]"
            )
        if not (self.weight_min <= self.new_value <= self.weight_max):
            raise ValueError(
                f"new_value {self.new_value} outside "
                f"[{self.weight_min}, {self.weight_max}]"
            )
        expected_new = self.previous_value + self.applied_delta
        if abs(expected_new - self.new_value) > _MUTATION_EPSILON:
            raise ValueError(
                f"new_value {self.new_value} != previous_value + applied_delta "
                f"({expected_new})"
            )
        # STEP 4: target-aware lock_key validation
        target = self.proposal.target
        if target == "synapse_weight":
            if not self.lock_key.startswith("synapse_weight:"):
                raise ValueError(
                    f"lock_key for synapse_weight must start with 'synapse_weight:', "
                    f"got {self.lock_key!r}"
                )
        elif target == "ifom_ttl":
            if not self.lock_key.startswith("ifom_ttl:"):
                raise ValueError(
                    f"lock_key for ifom_ttl must start with 'ifom_ttl:', "
                    f"got {self.lock_key!r}"
                )
        else:
            raise ValueError(
                f"Unknown proposal.target for RPEMutationRecord: {target!r}"
            )


# ---------------------------------------------------------------------------
# STEP 3.2: Pipeline snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RPEPipelineSnapshot:
    """Immutable snapshot of pipeline state at RPE measurement time.

    Built by RPEMutationPipelineWrapper after AsyncSwarm.execute() returns.
    Passed to DopamineRPE.apply() via to_rpe_context().

    Phase 6 STEP 3.2: pipeline integration only. No mutation logic here.
    RPEDecision observe-only invariant is fully preserved.

    Fields:
        pfc_active, pfc_cue_type, pfc_hint_applied: STEP 3.2 leaves these
            at default (False / None) — not yet surfaced from SwarmResult.
            Extension slot for STEP 3.3+.
    """

    trace_id: str
    session_id: str
    category: str | None
    difficulty: int
    response_source: str
    latency_ms: float
    error_occurred: bool
    timeout_occurred: bool
    continuation_bypass: bool
    pfc_active: bool
    pfc_cue_type: str | None
    pfc_hint_applied: bool
    # B13 — observable success signals (defaulted; _build_snapshot populates them
    # from SwarmResult). Additive so existing snapshot constructions still hold.
    planner_ok: bool = False
    generator_ok: bool = False
    context_ok: bool = False
    clean_finish: bool = False
    context_mean_similarity: float = 0.0

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {self.latency_ms}")
        if self.difficulty < 0:
            raise ValueError(f"difficulty must be >= 0, got {self.difficulty}")

    def to_rpe_context(self) -> "RPEContext":
        """Convert snapshot to RPEContext for DopamineRPE."""
        return RPEContext(
            trace_id=self.trace_id,
            session_id=self.session_id,
            category=self.category,
            difficulty=self.difficulty,
            response_source=self.response_source,
            latency_ms=self.latency_ms,
            error_occurred=self.error_occurred,
            timeout_occurred=self.timeout_occurred,
            continuation_bypass=self.continuation_bypass,
            pfc_active=self.pfc_active,
            pfc_cue_type=self.pfc_cue_type,
            pfc_hint_applied=self.pfc_hint_applied,
            planner_ok=self.planner_ok,
            generator_ok=self.generator_ok,
            context_ok=self.context_ok,
            clean_finish=self.clean_finish,
            context_mean_similarity=self.context_mean_similarity,
        )

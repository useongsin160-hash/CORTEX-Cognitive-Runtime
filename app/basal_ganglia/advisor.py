"""BasalGanglia Advisor.

Phase 6 STEP 5.1.

Read-only / recommendation-only orchestrator over ActionSelectionPolicy.

Isolation:
- No imports from app.routing.pfc, app.routing.lc, app.execution.swarm,
  app.api.*, app.main, app.rpe.*, app.memory.*, app.synapse.*.
- Live snapshot objects (PFC decision, LC result) are accessed via
  getattr/typing.Any only — no concrete type imports.
- Inputs are NEVER mutated.

Production behavior change: 0.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.basal_ganglia.models import (
    ActionCandidate,
    ActionSelectionContext,
    ActionSelectionDecision,
    JsonScalar,
)
from app.basal_ganglia.policies import ActionSelectionPolicy
from app.core.logging import SpinalLogger

_DEFAULT_CANDIDATE_TYPES: tuple[str, ...] = (
    "swarm_full",
    "swarm_minimal",
    "tier_1_5_augment",
    "fallback",
)

# Candidate compute ladder → route_path band. Pure label bridge (no app.routing
# import — these are plain strings). The ladder mirrors the band order
# lightweight < standard < full_pipeline; the two lightest candidates both land on
# lightweight (Tier-1.5 / cached fallback both run downstream of the lightweight
# branch).
_CANDIDATE_ROUTE_PATH: dict[str, str] = {
    "swarm_full": "full_pipeline",
    "swarm_minimal": "standard",
    "tier_1_5_augment": "lightweight",
    "fallback": "lightweight",
}


def route_path_for_candidate_type(candidate_type: str) -> str | None:
    """Map a BG candidate_type → the routing band it would recommend.

    DEFINED, NOT WIRED: this is the candidate_type↔route_path mapping the C2
    BG-apply stage (and the redesign measurement) consume. Production routing does
    NOT call it yet — the BG recommendation is still never applied
    (ActionSelectionDecision.applied stays False).

    Reverse-demotion safety (for C2): the existing routing ratchet floor (B11 S4)
    clamps any BG demotion UP, so a high-difficulty full_pipeline floor is never
    lowered by a lighter BG recommendation.
    """
    return _CANDIDATE_ROUTE_PATH.get(candidate_type)


def _mapping_to_pairs(
    mapping: Mapping[str, float] | None,
) -> tuple[tuple[str, float], ...]:
    """Convert a Mapping into an immutable tuple-of-pairs snapshot.

    The returned tuple is decoupled from the source mapping — subsequent
    mutation of the source must not affect the returned snapshot.
    """
    if mapping is None:
        return ()
    # Materialize keys+values into a fresh tuple so subsequent mutation of the
    # mapping cannot leak into the dataclass.
    items = []
    for key, value in mapping.items():
        items.append((str(key), float(value)))
    return tuple(items)


def _safe_getattr_float(obj: Any, name: str) -> float | None:
    """getattr(obj, name) coerced to float if numeric, else None."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_getattr_str(obj: Any, name: str) -> str | None:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if isinstance(value, str) and value:
        return value
    return None


def _safe_getattr_bool(obj: Any, name: str, default: bool = False) -> bool:
    if obj is None:
        return default
    value = getattr(obj, name, default)
    return bool(value)


def build_action_selection_context_from_snapshots(
    *,
    trace_id: str,
    session_id: str | None,
    category: str | None,
    difficulty: int,
    pfc_snapshot: Any | None = None,
    lc_snapshot: Any | None = None,
    synapse_weights: Mapping[str, float] | None = None,
    ifom_ttl_overrides: Mapping[str, float] | None = None,
    rpe_recent_positive_count: int = 0,
    rpe_recent_negative_count: int = 0,
    metadata: tuple[tuple[str, JsonScalar], ...] = (),
) -> ActionSelectionContext:
    """Build an ActionSelectionContext from primitive + duck-typed inputs.

    pfc_snapshot / lc_snapshot are inspected via getattr only — no concrete
    type imports. None values are safely handled.
    """
    pfc_active = _safe_getattr_bool(pfc_snapshot, "pfc_active", default=False)
    if pfc_snapshot is not None and not hasattr(pfc_snapshot, "pfc_active"):
        # Some snapshots may signal active via presence of cue_type/hint fields.
        pfc_active = (
            _safe_getattr_str(pfc_snapshot, "cue_type") is not None
            or _safe_getattr_float(pfc_snapshot, "confidence") is not None
        )
    pfc_cue_type = _safe_getattr_str(pfc_snapshot, "cue_type")
    pfc_confidence = _safe_getattr_float(pfc_snapshot, "confidence")
    pfc_intent_category = _safe_getattr_str(pfc_snapshot, "intent_category")

    lc_ne_level = _safe_getattr_float(lc_snapshot, "ne_level")
    lc_intent_label = _safe_getattr_str(lc_snapshot, "intent_label")

    return ActionSelectionContext(
        trace_id=trace_id,
        session_id=session_id,
        category=category,
        difficulty=int(difficulty),
        pfc_active=pfc_active,
        pfc_cue_type=pfc_cue_type,
        pfc_confidence=pfc_confidence,
        pfc_intent_category=pfc_intent_category,
        lc_ne_level=lc_ne_level,
        lc_intent_label=lc_intent_label,
        synapse_weights=_mapping_to_pairs(synapse_weights),
        ifom_ttl_overrides=_mapping_to_pairs(ifom_ttl_overrides),
        rpe_recent_positive_count=int(rpe_recent_positive_count),
        rpe_recent_negative_count=int(rpe_recent_negative_count),
        metadata=metadata,
    )


class BasalGangliaAdvisor:
    """Read-only action selection advisor.

    evaluate() scores candidates and produces a recommendation. Inputs are
    never mutated. ActionSelectionDecision.applied is hard-locked to False.
    """

    MODULE_NAME = "basal_ganglia"

    def __init__(
        self,
        policy: ActionSelectionPolicy | None = None,
        logger: SpinalLogger | None = None,
    ) -> None:
        self._policy = policy if policy is not None else ActionSelectionPolicy()
        self._logger = logger

    @property
    def policy(self) -> ActionSelectionPolicy:
        return self._policy

    async def evaluate(
        self,
        context: ActionSelectionContext,
        candidates: tuple[ActionCandidate, ...] | None = None,
    ) -> ActionSelectionDecision:
        """Score candidates and return a (recommendation-only) decision.

        candidates=None → derive a default candidate set from context.
        General exceptions are logged as bg.error and a fail-safe empty
        decision is returned. asyncio.CancelledError is always re-raised.
        """
        try:
            actual_candidates = (
                candidates
                if candidates is not None
                else self._build_default_candidates(context)
            )
            selected, confidence, reason = self._policy.select(
                context, actual_candidates
            )
            decision = ActionSelectionDecision(
                context=context,
                candidates=actual_candidates,
                selected=selected,
                confidence=confidence,
                reason=reason,
                applied=False,
            )
            await self._safe_log_event(
                trace_id=context.trace_id,
                event_type="bg.evaluated",
                payload={
                    "trace_id": context.trace_id,
                    "candidates_count": len(actual_candidates),
                    "selected_id": selected.candidate_id if selected else None,
                    "selected_type": selected.candidate_type if selected else None,
                    "confidence": confidence,
                    "reason": reason,
                    "category": context.category,
                    "applied": False,
                },
            )
            return decision
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=getattr(context, "trace_id", "unknown"),
                event_type="bg.error",
                payload={
                    "trace_id": getattr(context, "trace_id", "unknown"),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "applied": False,
                },
            )
            # Fail-safe empty decision.
            return ActionSelectionDecision(
                context=context,
                candidates=(),
                selected=None,
                confidence=0.0,
                reason="error",
                applied=False,
            )

    def _build_default_candidates(
        self, context: ActionSelectionContext
    ) -> tuple[ActionCandidate, ...]:
        """Build one candidate per ActionCandidateType using context snapshots.

        The per-candidate synapse/pfc/lc/rpe fields are SNAPSHOT-ONLY now — kept
        for the telemetry record. The redesigned policy derives its compute demand
        from the context (not these copied fields), so they are identical across
        candidates and no longer drive selection (the candidate_type does, via its
        compute level). See ActionSelectionPolicy.
        """
        synapse_weight = None
        if context.category is not None:
            for key, value in context.synapse_weights:
                if key == context.category:
                    synapse_weight = float(value)
                    break

        out: list[ActionCandidate] = []
        for ctype in _DEFAULT_CANDIDATE_TYPES:
            out.append(
                ActionCandidate(
                    candidate_id=f"default:{ctype}",
                    candidate_type=ctype,  # type: ignore[arg-type]
                    target_category=context.category,
                    synapse_weight=synapse_weight,
                    pfc_confidence=context.pfc_confidence,
                    lc_ne_level=context.lc_ne_level,
                    rpe_recent_positive_count=context.rpe_recent_positive_count,
                    rpe_recent_negative_count=context.rpe_recent_negative_count,
                )
            )
        return tuple(out)

    async def _safe_log_event(
        self,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if self._logger is None:
            return
        try:
            await self._logger.log_event(
                trace_id=trace_id,
                module_name=self.MODULE_NAME,
                event_type=event_type,
                payload=payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

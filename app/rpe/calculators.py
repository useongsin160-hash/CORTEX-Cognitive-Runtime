"""RPE dry-run calculators.

Phase 6 STEP 2: SynapseWeightDryRunCalculator.
Phase 6 STEP 4: IFOMTTLDryRunCalculator.

No import from app.synapse, app.memory, app.routing, app.api, app.main.
current_value is injected externally — this module never reads any store.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.rpe.difficulty_store import build_cat_diff_target_key
from app.rpe.ifom_store import IFOMTTLType, build_ifom_ttl_target_key
from app.rpe.models import (
    DryRunConfig,
    RPEDecision,
    RPEProposal,
    _clamp,
)

_DEFAULT_CONFIG = DryRunConfig()


class SynapseWeightDryRunCalculator:
    """Compute a dry-run RPEProposal for synapse_weight target.

    Delta formula:
        proposed_delta = clamp(
            prediction_error * confidence * max_delta,
            -max_delta, +max_delta
        )

    Proposed value (when current_value provided):
        proposed_value = clamp(
            current_value + proposed_delta,
            weight_min, weight_max
        )

    Skip conditions (return None):
        - "synapse_weight" not in config.enabled_targets
        - context.category is None or empty
        - context.category not in config.allowed_categories
        - current_value is not None and out of [weight_min, weight_max]
    """

    def __init__(
        self,
        config: DryRunConfig | None = None,
        rollback_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config if config is not None else _DEFAULT_CONFIG
        self._rollback_id_factory = rollback_id_factory or (
            lambda: str(uuid.uuid4())
        )

    def compute_proposal(
        self,
        decision: RPEDecision,
        current_value: float | None = None,
    ) -> RPEProposal | None:
        """Return an RPEProposal or None if any skip condition is met."""
        cfg = self._config

        # 1. Target not enabled → skip.
        if "synapse_weight" not in cfg.enabled_targets:
            return None

        # 2. Category validation → skip.
        category = decision.context.category
        if not category:
            return None
        if category not in cfg.allowed_categories:
            return None

        # 3. current_value bounds check → skip.
        if current_value is not None:
            if not (cfg.synapse_weight_min <= current_value <= cfg.synapse_weight_max):
                return None

        # 4. Compute proposed_delta.
        pe = decision.reward.prediction_error
        conf = decision.reward.confidence
        raw = pe * conf * cfg.max_delta
        proposed_delta = _clamp(raw, -cfg.max_delta, cfg.max_delta)

        # 5. Compute proposed_value.
        if current_value is not None:
            proposed_value: float | None = _clamp(
                current_value + proposed_delta,
                cfg.synapse_weight_min,
                cfg.synapse_weight_max,
            )
        else:
            proposed_value = None

        # 6. Generate rollback_id.
        rollback_id = self._rollback_id_factory()

        return RPEProposal(
            decision=decision,
            target="synapse_weight",
            target_key=f"category:{category}",
            current_value=current_value,
            proposed_delta=proposed_delta,
            proposed_value=proposed_value,
            max_delta=cfg.max_delta,
            rollback_id=rollback_id,
            confidence=decision.reward.confidence,
            applied=False,
        )


class SynapseDifficultyDryRunCalculator:
    """Dry-run RPEProposal for the cat×difficulty 35-cell store (B11 S1).

    Same delta formula as SynapseWeightDryRunCalculator —
        proposed_delta = clamp(prediction_error * confidence * max_delta,
                               -max_delta, +max_delta)
    difficulty does NOT enter the delta (emergent invariant); it only selects the
    cell address via ``category:{cat}:difficulty:{d}``. Additive: the category-only
    calculator is untouched.

    Skip conditions (return None):
        - "synapse_weight" not in config.enabled_targets
        - context.category is None/empty or not in config.allowed_categories
        - context.difficulty < 1 (unset/0 → never create a cell)
        - current_value is not None and out of [weight_min, weight_max]
    """

    def __init__(
        self,
        config: DryRunConfig | None = None,
        rollback_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config if config is not None else _DEFAULT_CONFIG
        self._rollback_id_factory = rollback_id_factory or (
            lambda: str(uuid.uuid4())
        )

    def compute_proposal(
        self,
        decision: RPEDecision,
        current_value: float | None = None,
    ) -> RPEProposal | None:
        cfg = self._config

        if "synapse_weight" not in cfg.enabled_targets:
            return None

        category = decision.context.category
        if not category:
            return None
        if category not in cfg.allowed_categories:
            return None

        difficulty = decision.context.difficulty
        if difficulty < 1:
            return None

        if current_value is not None:
            if not (cfg.synapse_weight_min <= current_value <= cfg.synapse_weight_max):
                return None

        pe = decision.reward.prediction_error
        conf = decision.reward.confidence
        raw = pe * conf * cfg.max_delta
        proposed_delta = _clamp(raw, -cfg.max_delta, cfg.max_delta)

        if current_value is not None:
            proposed_value: float | None = _clamp(
                current_value + proposed_delta,
                cfg.synapse_weight_min,
                cfg.synapse_weight_max,
            )
        else:
            proposed_value = None

        rollback_id = self._rollback_id_factory()

        return RPEProposal(
            decision=decision,
            target="synapse_weight",
            target_key=build_cat_diff_target_key(category, difficulty),
            current_value=current_value,
            proposed_delta=proposed_delta,
            proposed_value=proposed_value,
            max_delta=cfg.max_delta,
            rollback_id=rollback_id,
            confidence=decision.reward.confidence,
            applied=False,
        )


class IFOMTTLDryRunCalculator:
    """Compute a dry-run RPEProposal for ifom_ttl target.

    Phase 6 STEP 4.

    Delta formula (same shape as synapse_weight):
        proposed_delta = clamp(
            prediction_error * confidence * ifom_ttl_max_delta,
            -ifom_ttl_max_delta, +ifom_ttl_max_delta
        )

    Proposed value (when current_value provided):
        proposed_value = clamp(
            current_value + proposed_delta,
            ifom_ttl_min_seconds, ifom_ttl_max_seconds
        )

    Skip conditions (return None):
        - "ifom_ttl" not in config.enabled_targets
        - context.category is None or empty
        - context.category not in config.allowed_categories

    target_key format: ``{ttl_type}:{category}`` (from build_ifom_ttl_target_key).
    lock_key format (set by service): ``ifom_ttl:{target_key}``.
    """

    def __init__(
        self,
        config: DryRunConfig | None = None,
        rollback_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config if config is not None else DryRunConfig()
        self._rollback_id_factory = rollback_id_factory or (
            lambda: str(uuid.uuid4())
        )

    def compute_proposal(
        self,
        decision: RPEDecision,
        ttl_type: IFOMTTLType = "active",
        current_value: float | None = None,
    ) -> RPEProposal | None:
        """Return an RPEProposal for the given ttl_type, or None if skipped."""
        cfg = self._config

        # 1. Target not enabled → skip.
        if "ifom_ttl" not in cfg.enabled_targets:
            return None

        # 2. Category validation → skip.
        category = decision.context.category
        if not category:
            return None
        if category not in cfg.allowed_categories:
            return None

        # 3. Compute proposed_delta.
        pe = decision.reward.prediction_error
        conf = decision.reward.confidence
        raw = pe * conf * cfg.ifom_ttl_max_delta
        proposed_delta = _clamp(raw, -cfg.ifom_ttl_max_delta, cfg.ifom_ttl_max_delta)

        # 4. Compute proposed_value.
        if current_value is not None:
            proposed_value: float | None = _clamp(
                current_value + proposed_delta,
                cfg.ifom_ttl_min_seconds,
                cfg.ifom_ttl_max_seconds,
            )
        else:
            proposed_value = None

        # 5. Build target_key.
        target_key = build_ifom_ttl_target_key(ttl_type, category)

        # 6. Generate rollback_id.
        rollback_id = self._rollback_id_factory()

        return RPEProposal(
            decision=decision,
            target="ifom_ttl",
            target_key=target_key,
            current_value=current_value,
            proposed_delta=proposed_delta,
            proposed_value=proposed_value,
            max_delta=cfg.ifom_ttl_max_delta,
            rollback_id=rollback_id,
            confidence=decision.reward.confidence,
            applied=False,
        )

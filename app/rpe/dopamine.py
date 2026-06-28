"""DopamineRPE — observe-only orchestrator + dry-run + active wrapper.

Phase 6 STEP 1 contract (unchanged):
    - mode == "observe_only" enforced at construction.
    - observe(context) emits one RPEDecision per healthy source.
    - Each decision has applied=False, target=None, proposed_delta=None,
      rollback_id=None.
    - No mutation. No call into app/synapse, app/memory, app/routing.
    - asyncio.CancelledError is always re-raised.

Phase 6 STEP 2 addition:
    - dry_run(context, current_values) → list[RPEProposal].
    - Internally calls observe() to produce decisions.
    - Passes each decision to SynapseWeightDryRunCalculator.
    - Returns RPEProposal list; none are applied.
    - RPEDecision observe-only invariant is never weakened.

Phase 6 STEP 3.1 addition:
    - apply(context, current_values, mutation_service) → list[RPEMutationRecord].
    - Thin wrapper: dry_run() to produce proposals, then delegate to
      RPEMutationService.apply_proposals().
    - NOT a production entry point. Service unit only.
    - mutation_service=None or disabled → empty list + rpe.active_skipped.

Phase 6 STEP 4 addition:
    - dry_run() is now target-agnostic: computes both synapse_weight and
      ifom_ttl proposals if enabled in DryRunConfig.
    - IFOMTTLDryRunCalculator produces one proposal per TTL type (active,
      paused, completed, low_priority) when "ifom_ttl" is enabled.
    - current_values may contain ifom_ttl keys: "{ttl_type}:{category}".
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.core.logging import SpinalLogger
from app.rpe.calculators import IFOMTTLDryRunCalculator, SynapseWeightDryRunCalculator
from app.rpe.ifom_store import IFOMTTLType
from app.rpe.models import (
    DryRunConfig,
    RPEContext,
    RPEDecision,
    RPEMode,
    RPEMutationRecord,
    RPEProposal,
)
from app.rpe.sources import RewardSourceProtocol

if TYPE_CHECKING:
    from app.rpe.service import RPEMutationService

# IFOM TTL types to compute proposals for in each dry_run call.
_IFOM_TTL_TYPES: tuple[IFOMTTLType, ...] = (
    "active", "paused", "completed", "low_priority"
)


class DopamineRPE:
    MODULE_NAME = "dopamine_rpe"

    def __init__(
        self,
        sources: list[RewardSourceProtocol],
        logger: SpinalLogger,
        mode: RPEMode = "observe_only",
        dry_run_config: DryRunConfig | None = None,
    ) -> None:
        if mode != "observe_only":
            raise ValueError(
                f"STEP 1 invariant: mode must be 'observe_only', got {mode!r}"
            )
        self._sources = list(sources)
        self._logger = logger
        self._mode = mode
        self._dry_run_config = dry_run_config if dry_run_config is not None else DryRunConfig()
        self._calculator = SynapseWeightDryRunCalculator(config=self._dry_run_config)
        self._ifom_calculator = IFOMTTLDryRunCalculator(config=self._dry_run_config)

    @property
    def mode(self) -> RPEMode:
        return self._mode

    async def observe(self, context: RPEContext) -> list[RPEDecision]:
        decisions: list[RPEDecision] = []
        for source in self._sources:
            try:
                reward = await source.compute_reward(context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._safe_log_event(
                    context.trace_id,
                    "rpe.source_error",
                    {
                        "source_class": type(source).__name__,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                continue

            decision = RPEDecision(
                reward=reward,
                context=context,
                mode="observe_only",
            )
            decisions.append(decision)

            await self._safe_log_event(
                context.trace_id,
                "rpe.observed",
                {
                    "source": reward.source,
                    "expected_reward": reward.expected_reward,
                    "actual_reward": reward.actual_reward,
                    "prediction_error": reward.prediction_error,
                    "confidence": reward.confidence,
                    "category": context.category,
                    "response_source": context.response_source,
                    "pfc_active": context.pfc_active,
                    "continuation_bypass": context.continuation_bypass,
                },
            )
        return decisions

    async def dry_run(
        self,
        context: RPEContext,
        current_values: Mapping[str, float] | None = None,
    ) -> list[RPEProposal]:
        """Compute dry-run proposals for each observe-only decision.

        Target-agnostic (STEP 4): produces both synapse_weight and ifom_ttl
        proposals depending on DryRunConfig.enabled_targets.

        Proposals are never applied (applied=False).
        RPEDecision observe-only invariant is preserved.

        current_values keys:
            synapse_weight: "category:{category}"
            ifom_ttl:       "{ttl_type}:{category}"
        """
        decisions = await self.observe(context)
        proposals: list[RPEProposal] = []

        for decision in decisions:
            # --- synapse_weight proposals ---
            if "synapse_weight" in self._dry_run_config.enabled_targets:
                try:
                    sw_target_key = (
                        f"category:{context.category}" if context.category else None
                    )
                    sw_current_value = (
                        (current_values or {}).get(sw_target_key)  # type: ignore[arg-type]
                        if sw_target_key is not None
                        else None
                    )
                    sw_proposal = self._calculator.compute_proposal(
                        decision=decision,
                        current_value=sw_current_value,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._safe_log_event(
                        context.trace_id,
                        "rpe.dry_run_error",
                        {"error_type": type(exc).__name__, "error": str(exc)},
                    )
                    sw_proposal = None

                if sw_proposal is None:
                    skip_reason = self._resolve_skip_reason(
                        context, current_values, target="synapse_weight"
                    )
                    await self._safe_log_event(
                        context.trace_id,
                        "rpe.dry_run_skipped",
                        {
                            "reason": skip_reason,
                            "source": decision.reward.source,
                            "category": context.category,
                            "target": "synapse_weight",
                        },
                    )
                else:
                    proposals.append(sw_proposal)
                    await self._log_proposal(context, decision, sw_proposal)

            # --- ifom_ttl proposals (one per TTL type) ---
            if "ifom_ttl" in self._dry_run_config.enabled_targets:
                for ttl_type in _IFOM_TTL_TYPES:
                    try:
                        from app.rpe.ifom_store import build_ifom_ttl_target_key
                        ifom_target_key = build_ifom_ttl_target_key(
                            ttl_type, context.category
                        )
                        ifom_current_value = (current_values or {}).get(ifom_target_key)
                        ifom_proposal = self._ifom_calculator.compute_proposal(
                            decision=decision,
                            ttl_type=ttl_type,
                            current_value=ifom_current_value,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        await self._safe_log_event(
                            context.trace_id,
                            "rpe.dry_run_error",
                            {"error_type": type(exc).__name__, "error": str(exc)},
                        )
                        ifom_proposal = None

                    if ifom_proposal is None:
                        skip_reason = self._resolve_skip_reason(
                            context, current_values, target="ifom_ttl"
                        )
                        await self._safe_log_event(
                            context.trace_id,
                            "rpe.dry_run_skipped",
                            {
                                "reason": skip_reason,
                                "source": decision.reward.source,
                                "category": context.category,
                                "target": f"ifom_ttl:{ttl_type}",
                            },
                        )
                    else:
                        proposals.append(ifom_proposal)
                        await self._log_proposal(context, decision, ifom_proposal)

        return proposals

    async def _log_proposal(
        self,
        context: RPEContext,
        decision: RPEDecision,
        proposal: RPEProposal,
    ) -> None:
        await self._safe_log_event(
            context.trace_id,
            "rpe.dry_run_proposed",
            {
                "source": proposal.decision.reward.source,
                "target": proposal.target,
                "target_key": proposal.target_key,
                "current_value": proposal.current_value,
                "proposed_delta": proposal.proposed_delta,
                "proposed_value": proposal.proposed_value,
                "max_delta": proposal.max_delta,
                "rollback_id": proposal.rollback_id,
                "confidence": proposal.confidence,
                "prediction_error": decision.reward.prediction_error,
                "category": context.category,
                "applied": False,
            },
        )

    async def apply(
        self,
        context: RPEContext,
        current_values: Mapping[str, float] | None = None,
        mutation_service: "RPEMutationService | None" = None,
    ) -> list[RPEMutationRecord]:
        """Active mutation wrapper (STEP 3.1).

        NOT a production entry point. Tests and explicit service callers only.
        Delegates real mutation logic to RPEMutationService.apply_proposals().
        """
        if mutation_service is None:
            await self._safe_log_event(
                context.trace_id,
                "rpe.active_skipped",
                {"reason": "no_service"},
            )
            return []

        proposals = await self.dry_run(context, current_values)
        if not proposals:
            return []

        return await mutation_service.apply_proposals(
            proposals=proposals,
            current_values=current_values,
        )

    def _resolve_skip_reason(
        self,
        context: RPEContext,
        current_values: Mapping[str, float] | None,
        target: str = "synapse_weight",
    ) -> str:
        cfg = self._dry_run_config
        if not context.category:
            return "no_category"
        if context.category not in cfg.allowed_categories:
            return "invalid_category"
        if target == "synapse_weight":
            target_key = f"category:{context.category}"
            cv = (current_values or {}).get(target_key)
            if cv is not None and not (
                cfg.synapse_weight_min <= cv <= cfg.synapse_weight_max
            ):
                return "invalid_current_value"
        return "disabled_target"

    async def _safe_log_event(
        self,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
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

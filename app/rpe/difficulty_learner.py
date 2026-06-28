"""RPE difficulty learner (B11 S2).

Post-response learning into the category×difficulty 35-cell store. Mirrors
DopamineRPE.apply but for the difficulty path: observe (reward) →
SynapseDifficultyDryRunCalculator → a dedicated RPEMutationService bound to the
SynapseDifficultyWeightMutator. Gated by difficulty_learning_enabled; the frozen
7-cell production path and DopamineRPE/service used there are untouched.

Isolation: imports only RPE-internal pure modules + logging. No app.api /
app.main / app.routing / app.memory.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.core.logging import SpinalLogger
from app.rpe.calculators import SynapseDifficultyDryRunCalculator
from app.rpe.models import RPEContext, RPEMutationRecord

if TYPE_CHECKING:
    from app.rpe.dopamine import DopamineRPE
    from app.rpe.service import RPEMutationService


class RPEDifficultyLearner:
    """Drive one cat×difficulty learning step from a post-response RPEContext."""

    MODULE_NAME = "rpe_difficulty_learner"

    def __init__(
        self,
        dopamine_rpe: "DopamineRPE",
        calculator: SynapseDifficultyDryRunCalculator,
        service: "RPEMutationService",
        logger: SpinalLogger,
    ) -> None:
        self._dopamine = dopamine_rpe
        self._calculator = calculator
        self._service = service
        self._logger = logger

    async def learn(self, context: RPEContext) -> list[RPEMutationRecord]:
        """Observe → propose (current cell) → apply to the 35-cell store.

        Returns the applied records (possibly empty). No-op when learning is
        disabled, or the context lacks a session / category / valid difficulty.
        Reuses DopamineRPE.observe for the reward signal; the difficulty
        calculator addresses the (category, difficulty) cell and the dedicated
        service applies under its own single-apply / lock registry.
        """
        if not self._service.config.difficulty_learning_enabled:
            return []
        if (
            context.session_id is None
            or not context.category
            or context.difficulty < 1
        ):
            return []

        decisions = await self._dopamine.observe(context)
        proposals = []
        for decision in decisions:
            try:
                proposal = self._calculator.compute_proposal(decision, current_value=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._safe_log_event(
                    context.trace_id,
                    "rpe.difficulty_learn_error",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
                proposal = None
            if proposal is not None:
                proposals.append(proposal)

        if not proposals:
            return []

        # current_values=None: the mutator re-reads the cell under lock (seeding
        # an unlearned cell to its neutral seed). Nothing is written unless a
        # proposal qualifies and actually mutates.
        return await self._service.apply_proposals(proposals, current_values=None)

    async def _safe_log_event(
        self, trace_id: str, event_type: str, payload: dict[str, Any]
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

"""Synapse difficulty gate (B11 S2).

Read-only overlay run before routing/swarm: read the current (category,
difficulty) cell from the 35-cell learning store and overlay it onto
``TaskContext.synapse_snapshot`` for that category, so CategorySelector →
ContextAgent reflects the learned focus (B12 이후 가중치의 유일한 production 소비처).

Rules:
- Only the CURRENT (category, difficulty) cell is overlaid — the 35 cells are
  never merged/averaged (preserves difficulty separation).
- Unlearned cell (store returns None) → no-op; the SynapseObserver value stands.
- Reads only. Production SynapseState is untouched — this mutates the per-request
  TaskContext.synapse_snapshot dict (the LC-stamped copy) and nothing else.

Isolation: imports only app.api.schemas.context (TaskContext), app.core.logging,
and the RPE difficulty store protocol. No memory / routing / swarm / main imports.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.rpe.difficulty_store import SynapseDifficultyWeightStoreProtocol


class SynapseDifficultyGate:
    MODULE_NAME = "synapse_difficulty_gate"

    def __init__(
        self,
        store: SynapseDifficultyWeightStoreProtocol,
        logger: SpinalLogger | None = None,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._logger = logger
        self._enabled = enabled

    async def overlay(self, task_context: TaskContext, session_id: str) -> None:
        """Overlay the current (category, difficulty) learned weight, if any."""
        if not self._enabled:
            return
        category = task_context.category
        difficulty = int(task_context.difficulty)
        if not category or difficulty < 1 or session_id is None:
            return

        learned = await self._store.read_weight(session_id, category, difficulty)
        if learned is None:
            return  # unlearned → leave the SynapseObserver value in place

        previous = task_context.synapse_snapshot.get(category)
        task_context.synapse_snapshot[category] = float(learned)
        await self._safe_log_event(
            task_context.trace_id,
            "rpe.difficulty_gate_overlay",
            {
                "category": category,
                "difficulty": difficulty,
                "previous": previous,
                "overlaid": float(learned),
            },
        )

    async def _safe_log_event(
        self, trace_id: str, event_type: str, payload: dict[str, Any]
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

"""RPE biological routing override — label only (B11 S3a).

Shifts the skip_router path band ±1 from the learned (category, difficulty) cell
weight. **Label only**: changes ``RouteDecision.path``/``reason`` for telemetry;
execution does not yet consume the path (wired in S3b). The B12 난이도→tier 1:1
mapping is NOT touched — only the skip_router physical-path label moves.

Store-direct read: an UNLEARNED cell (store returns None) yields NO override — the
B12 difficulty path stands. This is exactly what keeps a high-difficulty first
question on full_pipeline (no seed-0.3 leak): None (unlearned) is distinct from a
learned-low value (<0.4).

This module lives in app/routing (NOT app/rpe) because it imports RouteDecision;
the RPE isolation invariant forbids app.rpe importing app.routing. The reverse
direction — routing reading app.rpe.difficulty_store — is allowed.

⚠️ S3a has no ratchet/decay yet (S4/S5): a learned-low cell demotes freely; the
session no-demote floor and forgetting-release come next.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.rpe.difficulty_store import SynapseDifficultyWeightStoreProtocol
from app.routing.skip_router import RouteDecision

# Physical path bands, low → high. An override moves at most one band.
_BANDS: tuple[str, ...] = ("lightweight", "standard", "full_pipeline")
_DEMOTE_BELOW = 0.4  # matches the CategorySelector threshold
_PROMOTE_AT = 0.7


class DifficultyRouteOverride:
    MODULE_NAME = "rpe_route_override"

    def __init__(
        self,
        store: SynapseDifficultyWeightStoreProtocol,
        logger: SpinalLogger | None = None,
        *,
        demote_below: float = _DEMOTE_BELOW,
        promote_at: float = _PROMOTE_AT,
    ) -> None:
        self._store = store
        self._logger = logger
        self._demote_below = demote_below
        self._promote_at = promote_at

    async def apply(
        self,
        decision: RouteDecision,
        task_context: TaskContext,
        session_id: str,
    ) -> RouteDecision:
        """Return a (possibly band-shifted) RouteDecision. Label only."""
        category = task_context.category
        difficulty = int(task_context.difficulty)
        if not category or difficulty < 1 or session_id is None:
            return decision
        if decision.path not in _BANDS:
            return decision

        learned = await self._store.read_weight(session_id, category, difficulty)
        if learned is None:
            return decision  # unlearned → no override (B12 path stands; no leak)

        idx = _BANDS.index(decision.path)
        if learned < self._demote_below:
            new_idx = max(0, idx - 1)
            direction = "demote"
        elif learned >= self._promote_at:
            new_idx = min(len(_BANDS) - 1, idx + 1)
            direction = "promote"
        else:
            return decision  # mid band → keep

        if new_idx == idx:
            return decision  # clamped at an edge — no actual shift

        new_path = _BANDS[new_idx]
        new_decision = decision.model_copy(
            update={
                "path": new_path,
                # skip_layers intentionally preserved — execution meaning is S3b.
                "reason": (
                    f"{decision.reason} | rpe_override={direction} "
                    f"{decision.path}->{new_path} (w={learned:.3f})"
                ),
            }
        )
        await self._safe_log_event(
            task_context.trace_id,
            "rpe.route_override",
            {
                "category": category,
                "difficulty": difficulty,
                "weight": float(learned),
                "direction": direction,
                "from_path": decision.path,
                "to_path": new_path,
            },
        )
        return new_decision

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

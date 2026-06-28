"""Monotonic routing ratchet — session no-demote (B11 S4).

Asymmetry: **rise = learning, fall = forgetting (S5 decay only)**. Once a cell's
route band rises (via the RPE override or its B12-native baseline), it is locked
against demotion for the lifetime of the session. A learned-low cell can no longer
freely demote (the "무방비 강등" S3a/S3b left open) — it stays at its floor until
S5 decay lowers the floor.

floor key = (session_id, category, difficulty) — matches the 35-cell learning /
override granularity, so coding:HARD's floor never forces coding:EASY up (no
over-protection). B12-native baseline is included: the first request stamps the
floor at the difficulty-derived path, so difficulty 4·5 (full_pipeline) is
structurally protected from leaking to a shortcut.

Bounded: an LRU over sessions (MAX_SESSIONS) so the floor map never grows
unbounded (8GB host). This deliberately does NOT inherit the no-GC debt of
SynapseStore / difficulty_store.

Lives in app/routing (not app/rpe) — it imports RouteDecision and the RPE
isolation invariant forbids app.rpe importing app.routing.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.routing.skip_router import RouteDecision

# Physical path bands, low → high (mirrors rpe_route_override._BANDS; kept local to
# avoid importing a private symbol). The ratchet moves only UP along this order.
_BANDS: tuple[str, ...] = ("lightweight", "standard", "full_pipeline")

# Upper bound on tracked sessions — LRU eviction beyond this (bounded memory).
MAX_SESSIONS: int = 512


class RoutingRatchet:
    MODULE_NAME = "routing_ratchet"

    def __init__(
        self,
        logger: SpinalLogger | None = None,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        # session_id → {(category, difficulty): floor_path}. OrderedDict = LRU.
        self._floors: "OrderedDict[str, dict[tuple[str, int], str]]" = OrderedDict()
        self._logger = logger
        self._max_sessions = max_sessions

    @staticmethod
    def _band_index(path: str | None) -> int:
        if path is None or path not in _BANDS:
            return 0
        return _BANDS.index(path)

    def _session_floors(self, session_id: str) -> dict[tuple[str, int], str]:
        """Return the session's floor map (LRU touch); create + evict if needed."""
        if session_id in self._floors:
            self._floors.move_to_end(session_id)
            return self._floors[session_id]
        self._floors[session_id] = {}
        if len(self._floors) > self._max_sessions:
            self._floors.popitem(last=False)  # evict least-recently-used session
        return self._floors[session_id]

    async def apply(
        self,
        decision: RouteDecision,
        task_context: TaskContext,
        session_id: str,
    ) -> RouteDecision:
        """Clamp the path UP to the session floor (no demote); raise the floor.

        Monotonic: the floor only ever rises. An override result below the floor
        is clamped up (demotion blocked); at/above the floor it passes and the
        floor follows. Returns the (possibly clamped) RouteDecision.
        """
        category = task_context.category
        difficulty = int(task_context.difficulty)
        if not category or difficulty < 1 or session_id is None:
            return decision
        if decision.path not in _BANDS:
            return decision

        cell = (category, difficulty)
        session_floors = self._session_floors(session_id)
        floor = session_floors.get(cell)

        override_idx = self._band_index(decision.path)
        floor_idx = self._band_index(floor)
        final_idx = max(override_idx, floor_idx)

        # Ratchet: the floor rises monotonically to the final path (B12-native
        # baseline included — the first request stamps the floor here).
        session_floors[cell] = _BANDS[final_idx]

        if final_idx == override_idx:
            return decision  # rise or unchanged — no clamp

        # Demotion blocked: clamp up to the floor.
        final_path = _BANDS[final_idx]
        await self._safe_log_event(
            task_context.trace_id,
            "rpe.ratchet_blocked_demote",
            {
                "category": category,
                "difficulty": difficulty,
                "override_path": decision.path,
                "floor_path": final_path,
            },
        )
        return decision.model_copy(
            update={
                "path": final_path,
                "reason": (
                    f"{decision.reason} | ratchet_floor={final_path} "
                    f"(blocked demote from {decision.path})"
                ),
            }
        )

    def lower_floor(
        self, session_id: str, category: str, difficulty: int, path: str
    ) -> None:
        """Set a cell's floor to ``path`` (a generic lowering primitive).

        Used by decay_release (S5). Separate from B4 (apscheduler mutation
        rollback). No-op if the session/cell has no floor yet.
        """
        session_floors = self._floors.get(session_id)
        if session_floors is None:
            return
        session_floors[(category, difficulty)] = path

    def decay_release(
        self, session_id: str, category: str, difficulty: int, baseline_path: str
    ) -> None:
        """S5 decay hook — lower the cell's floor ONE band, never below baseline.

        Called when decay forgets a cell (weight < threshold). Lowers the floor by
        a single band toward the difficulty's B12-native baseline so high
        difficulty stays protected (only learned promotions are forgotten). No-op
        if the session/cell has no floor yet. Bounded — never below lightweight.
        """
        session_floors = self._floors.get(session_id)
        if session_floors is None:
            return
        cell = (category, difficulty)
        current = session_floors.get(cell)
        if current is None:
            return
        new_idx = max(self._band_index(baseline_path), self._band_index(current) - 1)
        self.lower_floor(session_id, category, difficulty, _BANDS[new_idx])

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

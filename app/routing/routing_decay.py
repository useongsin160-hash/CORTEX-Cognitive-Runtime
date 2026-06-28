"""Step-based routing decay — lazy weight forgetting that releases the floor (B11 S5).

The counterpart of the S4 ratchet: rise = learning, **fall = forgetting**. Without
decay the ratchet would inflate every cell upward forever (all → full_pipeline).

Step-based (per request, not a wall-clock timer): as a session accumulates
requests, idle (category, difficulty) cells lose weight. Decay is realized LAZILY
— when a cell becomes current again, its accrued idle decay (since it was last
used) is applied in one O(1) step; other cells are not swept (no per-request
session scan, no difficulty_store index). A freshly/consecutively used cell has
idle 0 → no decay ("use it or lose it").

When a forgotten cell's weight drops below the release threshold, its routing
floor is lowered one band toward the difficulty's B12-native baseline (NOT below —
high difficulty stays protected; only learned promotions are forgotten). This is
how a session-locked cell (S4) regains the ability to demote.

emergent invariant: decay lowers weight only; it never touches the clamp bounds
(1.0 / 0.1). ⚠️ Distinct from B4 (apscheduler mutation rollback) — decay is an
in-request, step-based weight/floor operation, not a scheduler.

Lives in app/routing (reads difficulty_store, drives the routing ratchet).
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.rpe.difficulty_store import SynapseDifficultyWeightStoreProtocol

if TYPE_CHECKING:
    from app.routing.routing_ratchet import RoutingRatchet

# Start values (B6 측정 후 튜닝). rate 0.01/step, release threshold 0.4 = S3a demote
# threshold (consistent). weight floor 0.1 = emergent lower clamp.
DECAY_RATE: float = 0.01
RELEASE_THRESHOLD: float = 0.4
WEIGHT_MIN: float = 0.1
MAX_SESSIONS: int = 512


def _baseline_band(difficulty: int) -> str:
    """B12-native path band for a difficulty (mirrors skip_router): the floor that
    decay must NOT release below — high difficulty stays structurally protected."""
    if difficulty >= 4:
        return "full_pipeline"
    if difficulty >= 2:
        return "standard"
    return "lightweight"


class RoutingDecay:
    MODULE_NAME = "routing_decay"

    def __init__(
        self,
        store: SynapseDifficultyWeightStoreProtocol,
        ratchet: "RoutingRatchet",
        logger: SpinalLogger | None = None,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self._store = store
        self._ratchet = ratchet
        self._logger = logger
        self._max_sessions = max_sessions
        # session_id → {"step": int, "last_used": {(cat, diff): step}}. OrderedDict
        # = LRU; evicting a session drops its whole decay state (bounded memory).
        self._sessions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def _session_state(self, session_id: str) -> dict[str, Any]:
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]
        state: dict[str, Any] = {"step": 0, "last_used": {}}
        self._sessions[session_id] = state
        if len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)  # evict least-recently-used session
        return state

    async def step(self, task_context: TaskContext, session_id: str) -> None:
        """Realize the current cell's accrued idle decay (lazy, O(1)).

        Called at request entry, BEFORE the gate/override read the cell, so a
        forgotten cell can demote this request. Synchronous dict work (the store
        ops do not yield) — no interleave with the background learner.
        """
        category = task_context.category
        difficulty = int(task_context.difficulty)
        if not category or difficulty < 1 or session_id is None:
            return

        state = self._session_state(session_id)
        state["step"] += 1
        step = state["step"]
        cell = (category, difficulty)
        last_used: dict[tuple[str, int], int] = state["last_used"]
        last = last_used.get(cell)
        last_used[cell] = step  # mark current cell as used now (fresh)

        if last is None:
            return  # first time this cell is seen — nothing to forget
        idle = step - last - 1  # requests since last use, excluding both ends
        if idle <= 0:
            return  # consecutive / fresh use → no decay (current cell excluded)

        weight = await self._store.read_weight(session_id, category, difficulty)
        if weight is None:
            return  # unlearned cell — nothing to decay

        decayed = max(WEIGHT_MIN, weight - DECAY_RATE * idle)
        if decayed < weight:
            await self._store.write_weight(session_id, category, difficulty, decayed)

        if decayed < RELEASE_THRESHOLD:
            baseline = _baseline_band(difficulty)
            self._ratchet.decay_release(session_id, category, difficulty, baseline)
            await self._safe_log_event(
                task_context.trace_id,
                "rpe.decay_released",
                {
                    "category": category,
                    "difficulty": difficulty,
                    "idle_steps": idle,
                    "weight": decayed,
                    "baseline_floor": baseline,
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

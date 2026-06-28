"""Synapse 가중치 맵 저장/조회 — 세션별 격리 인메모리 저장소."""
from __future__ import annotations

from collections import OrderedDict

from pydantic import BaseModel, Field, field_validator

from app.synapse.categories import INITIAL_WEIGHT, SYNAPSE_CATEGORIES

# Bounded LRU over sessions so the in-memory map never grows unbounded on the
# 8GB host (mirrors routing_ratchet.MAX_SESSIONS). An evicted session re-seeds at
# the neutral 0.3 weight on next access — harmless under the frozen synapse path.
MAX_SESSIONS: int = 512


def _initial_weights() -> dict[str, float]:
    """All 7 categories seeded at the neutral 0.3 weight."""
    return {cat: INITIAL_WEIGHT for cat in sorted(SYNAPSE_CATEGORIES)}


class SynapseState(BaseModel):
    """Per-session synapse state. Pure JSON-serializable Pydantic model."""

    weights: dict[str, float] = Field(default_factory=_initial_weights)
    last_observed_category: str | None = None
    last_observed_similarity: float | None = None
    last_observed_embedding: list[float] | None = None
    flush_count: int = 0
    last_flush_at: float | None = None

    @field_validator("weights")
    @classmethod
    def _normalize_weights(cls, v: dict[str, float]) -> dict[str, float]:
        """Keep exactly the 7 known categories — unknown keys are dropped
        (무시), missing categories are backfilled to INITIAL_WEIGHT."""
        return {
            cat: float(v.get(cat, INITIAL_WEIGHT))
            for cat in sorted(SYNAPSE_CATEGORIES)
        }


class SynapseStore:
    """In-memory dict[session_id, SynapseState].

    Phase 3.5 assumes a single query per session at a time — concurrent
    same-session access is a Phase 4 Lock Manager concern. Sessions are
    fully isolated so no inter-session lock is needed.
    """

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        # OrderedDict = LRU: most-recently-used session at the end, evict from front.
        self._states: "OrderedDict[str, SynapseState]" = OrderedDict()
        self._max_sessions = max_sessions

    async def get_state(self, session_id: str) -> SynapseState:
        if session_id in self._states:
            self._states.move_to_end(session_id)  # LRU touch
            return self._states[session_id]
        self._states[session_id] = SynapseState()
        if len(self._states) > self._max_sessions:
            self._states.popitem(last=False)  # evict least-recently-used session
        return self._states[session_id]

    async def update_state(self, session_id: str, state: SynapseState) -> None:
        self._states[session_id] = state
        self._states.move_to_end(session_id)
        if len(self._states) > self._max_sessions:
            self._states.popitem(last=False)

    async def snapshot(self, session_id: str) -> dict[str, float]:
        """Extract the weight map as a plain JSON-safe dict."""
        state = await self.get_state(session_id)
        return dict(state.weights)

    async def reset_state(self, session_id: str) -> None:
        """Reset every category weight back to 0.3 (used on Flush)."""
        state = await self.get_state(session_id)
        state.weights = _initial_weights()

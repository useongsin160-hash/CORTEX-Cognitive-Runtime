"""Crossroad Reasoning (B8) — forced explore at a route-band crossroad.

Crossroad Reasoning (갈림길 추론), NOT the arbitration "Conflict Resolution" of
ADR-014 (a different, still-deferred component). The RPE override picks one route
band as #1; when the learned (category, difficulty) weight sits within a small
margin of a band threshold — a "crossroad" where the routing decision is nearly a
tie — CR probabilistically runs the ADJACENT (loser) band too, as a background
explore, and feeds its outcome into the 35-cell RPE learning. This injects
exploration so the system does not lock into exploit ("안 뽑던 길도 학습받게").

The user-facing answer is always the #1 band's; the explore is a learning-only
side run. Doubly frozen pre-C3: cr_enabled gates the explore execution, and the
explore's learning is gated again by difficulty_learning_enabled (B13 freeze), so
nothing fires or learns until C3 flips both.

Leaf orchestrator: it reads the 35-cell weight (difficulty_store) and spawns the
explore through an INJECTED runner (app.state.rpe_pipeline.execute), so it imports
neither app.rpe.pipeline nor app.execution.swarm. The explore reuses the existing
swarm + RPEDifficultyLearner — no new learning logic — and a distinct sub-trace_id
keeps its mutation off the main run's (trace_id, target_key) single-apply key.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.rpe.difficulty_store import SynapseDifficultyWeightStoreProtocol
from app.routing.skip_router import RouteDecision

# Mirror rpe_route_override's band ladder + thresholds (the weight→band map CR
# reasons about). low → high; an override moves at most one band.
_BANDS: tuple[str, ...] = ("lightweight", "standard", "full_pipeline")
_DEMOTE_THRESHOLD = 0.4  # below → demote (matches override / CategorySelector)
_PROMOTE_THRESHOLD = 0.7  # at/above → promote

# An injected coroutine that runs one swarm pass (app.state.rpe_pipeline.execute).
# Typed loosely so this module never imports app.rpe.pipeline / app.execution.
ExploreRunner = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class CrossroadConfig:
    """CR gating. enabled=False freezes the explore execution (C3 flips it)."""

    enabled: bool = False
    stable_probability: float = 0.10
    # 탐색(explore) 모드 확률 — LIVE: the PFC uncertainty signal is surfaced by the
    # routes-PFC run (B10) and widened in C4, so an uncertain PFC at a crossroad
    # fires the explore at this (higher) probability.
    explore_probability: float = 0.50
    margin: float = 0.05  # |weight - threshold| absolute window for a crossroad

    def __post_init__(self) -> None:
        for name in ("stable_probability", "explore_probability"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be in [0.0, 1.0], got {value}")
        if self.margin < 0:
            raise ValueError(f"margin must be >= 0, got {self.margin}")


class CrossroadReasoner:
    """Decides, per routed request, whether to fire a background band explore."""

    MODULE_NAME = "crossroad"

    def __init__(
        self,
        store: SynapseDifficultyWeightStoreProtocol,
        explore_runner: ExploreRunner,
        logger: SpinalLogger | None = None,
        config: CrossroadConfig | None = None,
        *,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._explore_runner = explore_runner
        self._logger = logger
        self._config = config if config is not None else CrossroadConfig()
        # Injected for deterministic tests; defaults to random.random.
        self._rng = rng if rng is not None else random.random
        # Strong refs keep fire-and-forget explore tasks from being GC'd.
        self._background_tasks: set[asyncio.Task] = set()

    @property
    def config(self) -> CrossroadConfig:
        return self._config

    async def maybe_explore(
        self,
        task_context: TaskContext,
        decision: RouteDecision,
        session_id: str,
        query_features: Any = None,
        *,
        pfc_explore: bool = False,
    ) -> None:
        """Decide (synchronously) whether to fire a background explore; if so,
        spawn it and return. No-op unless enabled, at a crossroad, not in emergency
        mode, and the probability roll passes. Never blocks on the explore swarm.

        Mode (B10): emergency (epinephrine/NE) → off; explore (pfc_explore, i.e. an
        uncertain PFC, surfaced by routes) → explore_probability; otherwise stable
        → stable_probability."""
        if not self._config.enabled:
            return  # ① frozen — no explore (C3 flips cr_enabled)
        category = task_context.category
        if not category or session_id is None:
            return
        if decision.path not in _BANDS:
            return
        difficulty = int(task_context.difficulty)
        if difficulty < 1:
            return
        weight = await self._store.read_weight(session_id, category, difficulty)
        if weight is None:
            return  # unlearned cell → no crossroad (B12 path stands)
        explore_band = self._crossroad_band(decision.path, float(weight))
        if explore_band is None:
            return  # weight not near a threshold, or clamped at a ladder edge
        if self._mode_blocks(task_context):
            return  # emergency mode (epinephrine / NE) → CR off
        # Mode selection: an uncertain PFC (pfc_explore, surfaced by the routes-PFC
        # run in B10) raises the fire probability to explore_probability; otherwise
        # the default stable mode uses stable_probability.
        probability = (
            self._config.explore_probability
            if pfc_explore
            else self._config.stable_probability
        )
        if self._rng() >= probability:
            return
        self._spawn_explore(
            task_context, decision.path, explore_band, session_id, query_features
        )

    def _crossroad_band(self, path: str, weight: float) -> str | None:
        """The adjacent band to explore, or None if there is no crossroad.

        A crossroad = the weight sits within `margin` of a band threshold, i.e.
        the override is nearly tipping the routing decision. Clamped at the ladder
        edges (lightweight can't demote; full_pipeline can't promote)."""
        idx = _BANDS.index(path)
        margin = self._config.margin
        if abs(weight - _DEMOTE_THRESHOLD) <= margin and idx > 0:
            return _BANDS[idx - 1]
        if abs(weight - _PROMOTE_THRESHOLD) <= margin and idx < len(_BANDS) - 1:
            return _BANDS[idx + 1]
        return None

    def _mode_blocks(self, task_context: TaskContext) -> bool:
        """Emergency mode disables CR. Mapped to the high-compute signals available
        here: epinephrine_active (limit-break / full_pipeline) or ne_boost
        (difficulty >= 4). The explore (PFC-directed) mode is selected separately
        via the pfc_explore arg (B10 surfaces it from the routes-PFC run)."""
        return bool(
            getattr(task_context, "epinephrine_active", False)
            or getattr(task_context, "ne_boost", False)
        )

    def _spawn_explore(
        self,
        task_context: TaskContext,
        from_path: str,
        explore_band: str,
        session_id: str,
        query_features: Any,
    ) -> None:
        sub_trace = f"{task_context.trace_id}::cr_explore"
        is_full = explore_band == "full_pipeline"
        # A faithful copy of the loser band: distinct trace (single-apply
        # separation), the explore route_path, and the band-consistent
        # epinephrine flag. The swarm overwrites context_agent_result, so the
        # inherited value is harmless.
        explore_ctx = task_context.model_copy(
            update={
                "trace_id": sub_trace,
                "route_path": explore_band,
                "epinephrine_active": is_full,
                "epinephrine_reason": "limit_break" if is_full else None,
            }
        )
        task = asyncio.create_task(
            self._run_explore(
                explore_ctx,
                from_path,
                explore_band,
                sub_trace,
                session_id,
                query_features,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_explore(
        self,
        explore_ctx: TaskContext,
        from_path: str,
        explore_band: str,
        sub_trace: str,
        session_id: str,
        query_features: Any,
    ) -> None:
        """Background explore: run the loser band via the injected runner
        (rpe_pipeline.execute → swarm + post-response learn on sub_trace). The
        response is discarded; only the learning side-effect matters. Fail-open;
        CancelledError re-raised."""
        try:
            await self._explore_runner(
                explore_ctx,
                query_features,
                trace_id=sub_trace,
                session_id=session_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                sub_trace,
                "cr.explore_error",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "from_path": from_path,
                    "explore_band": explore_band,
                },
            )
            return
        await self._safe_log_event(
            sub_trace,
            "cr.explore",
            {
                "from_path": from_path,
                "explore_band": explore_band,
                "sub_trace": sub_trace,
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

"""RPEMutationPipelineWrapper: post-response RPE background task integration.

Phase 6 STEP 3.2.

Responsibilities:
1. Delegate execution to inner_swarm.execute() and return its result unchanged.
2. Build an RPEPipelineSnapshot from the completed SwarmResult + context.
3. If mutation_service.config.observe_enabled=True, create an asyncio background
   task that calls DopamineRPE.apply() — fire-and-forget, fail-open. (Whether a
   proposal is actually applied is gated separately by active_enabled in the
   service; observe_enabled alone never mutates.)
4. Return the original SwarmResult (no schema changes).

Disabled-by-default guarantees:
- No background task is created when observe_enabled=False (production default).
- Background task errors are logged (rpe.pipeline_error) but never surface
  to the caller.
- asyncio.CancelledError is ALWAYS re-raised — never swallowed.

Isolation rules (STEP 3.2):
- pipeline.py may import app.execution.swarm_models (pure data) but must
  NOT import app.execution.swarm (no tight coupling to AsyncSwarm runtime).
- DopamineRPE / RPEMutationService are injected duck-typed; no direct
  coupling to their constructors here.
- app.api.routes, app.main, app.memory, app.routing remain forbidden.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.api.schemas.context import TaskContext
from app.api.schemas.query_features import QueryFeatures
from app.core.logging import SpinalLogger
from app.execution.swarm_models import SwarmResult
from app.rpe.models import RPEPipelineSnapshot

if TYPE_CHECKING:
    from app.rpe.difficulty_learner import RPEDifficultyLearner
    from app.rpe.dopamine import DopamineRPE
    from app.rpe.recent_counter import RPERecentCounter
    from app.rpe.service import RPEMutationService


@runtime_checkable
class _InnerSwarmProto(Protocol):
    """Minimal interface required from the wrapped swarm.

    AsyncSwarm satisfies this protocol at runtime (duck-typed).
    No import of app.execution.swarm required.
    """

    async def execute(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None = None,
    ) -> SwarmResult: ...


class RPEMutationPipelineWrapper:
    """Wraps an inner swarm with post-response RPE background task.

    Usage (created in app/main.py create_app):
        wrapper = RPEMutationPipelineWrapper(
            inner_swarm=async_swarm,
            dopamine_rpe=dopamine_rpe,
            mutation_service=rpe_mutation_service,   # enabled=False default
            logger=get_spinal_logger(),
        )
        app.state.rpe_pipeline = wrapper

    routes.py calls:
        await state.rpe_pipeline.execute(
            task_context=task_context,
            query_features=query_features,
            trace_id=trace_id,
            session_id=session_id,
        )

    Signature is a superset of AsyncSwarm.execute(): trace_id and session_id
    are keyword-only extras consumed by the wrapper (not forwarded to swarm).
    """

    MODULE_NAME = "rpe_pipeline"

    def __init__(
        self,
        inner_swarm: _InnerSwarmProto,
        dopamine_rpe: "DopamineRPE",
        mutation_service: "RPEMutationService",
        logger: SpinalLogger,
        difficulty_learner: "RPEDifficultyLearner | None" = None,
        recent_counter: "RPERecentCounter | None" = None,
    ) -> None:
        self._inner_swarm = inner_swarm
        self._dopamine_rpe = dopamine_rpe
        self._mutation_service = mutation_service
        self._logger = logger
        # B11 S2: optional category×difficulty learner. When present and the
        # service config enables difficulty learning, the post-response background
        # task also drives one 35-cell learning step (separate store).
        self._difficulty_learner = difficulty_learner
        # B10: optional read-side RPE recent counter. Fed the sign of each applied
        # learning mutation so the BG advisory pass has a real rpe_recent_* signal.
        self._recent_counter = recent_counter
        # Strong references prevent GC of pending background tasks.
        self._background_tasks: set[asyncio.Task] = set()

    async def execute(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None = None,
        *,
        trace_id: str,
        session_id: str,
    ) -> SwarmResult:
        """Execute inner_swarm; optionally schedule RPE background task.

        The SwarmResult is returned unchanged. The background RPE observe task
        runs only when mutation_service.config.observe_enabled=True; any actual
        mutation is gated independently by active_enabled in the service.
        """
        swarm_result = await self._inner_swarm.execute(task_context, query_features)

        # Build frozen snapshot while we have all the data.
        snapshot = self._build_snapshot(task_context, swarm_result, trace_id, session_id)

        # B5: the observe path is gated by observe_enabled (NOT active_enabled).
        # B11 S2: difficulty learning (35-cell, separate store) is gated by
        # difficulty_learning_enabled — independent of the frozen 7-cell observe/
        # active gates. The background task is spawned when EITHER is on; each
        # sub-path is re-checked inside _rpe_background. Production 7-cell stays
        # frozen (observe=active=False).
        cfg = self._mutation_service.config
        if cfg.observe_enabled or (
            cfg.difficulty_learning_enabled and self._difficulty_learner is not None
        ):
            bg_task = asyncio.create_task(self._rpe_background(snapshot))
            self._background_tasks.add(bg_task)
            bg_task.add_done_callback(self._background_tasks.discard)

        return swarm_result

    # ------------------------------------------------------------------
    # Snapshot construction
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        task_context: TaskContext,
        swarm_result: SwarmResult,
        trace_id: str,
        session_id: str,
    ) -> RPEPipelineSnapshot:
        statuses = [
            swarm_result.context_status,
            swarm_result.planner_status,
            swarm_result.generator_status,
        ]
        error_occurred = "error" in statuses
        timeout_occurred = "timeout" in statuses

        cont_ctx = task_context.continuation_context
        continuation_bypass = cont_ctx is not None and cont_ctx.detected

        # B13 — restore observable SUCCESS signals (the snapshot previously mined
        # only the failure complement). All are observed facts from SwarmResult,
        # not labels: a clean stage ("ok", not "fallback"), a clean generation
        # finish, and the mean relevance of the context actually used.
        gen = swarm_result.generator_result
        clean_finish = gen.finish_reason == "stop" and gen.fallback_candidate is None
        ctx_res = swarm_result.context_result
        if ctx_res is not None and ctx_res.retrieved:
            sims = [c.similarity for c in ctx_res.retrieved if not c.masked_by_gaba]
            context_mean_similarity = sum(sims) / len(sims) if sims else 0.0
        else:
            context_mean_similarity = 0.0

        return RPEPipelineSnapshot(
            trace_id=trace_id,
            session_id=session_id,
            category=task_context.category,
            difficulty=int(task_context.difficulty),
            response_source="swarm",
            latency_ms=swarm_result.total_elapsed_ms,
            error_occurred=error_occurred,
            timeout_occurred=timeout_occurred,
            continuation_bypass=continuation_bypass,
            # pfc_active / pfc_cue_type / pfc_hint_applied: extension slots
            # for STEP 3.3+. Not surfaced by TaskContext or SwarmResult today.
            pfc_active=False,
            pfc_cue_type=None,
            pfc_hint_applied=False,
            planner_ok=swarm_result.planner_status == "ok",
            generator_ok=swarm_result.generator_status == "ok",
            context_ok=swarm_result.context_status == "ok",
            clean_finish=clean_finish,
            context_mean_similarity=context_mean_similarity,
        )

    # ------------------------------------------------------------------
    # Background RPE task
    # ------------------------------------------------------------------

    async def _rpe_background(self, snapshot: RPEPipelineSnapshot) -> None:
        """Fire-and-forget RPE task. Errors are logged, never propagated.

        CancelledError is always re-raised.
        """
        try:
            context = snapshot.to_rpe_context()
            # 7-cell observe/dry-run/active path (frozen in production).
            if self._mutation_service.config.observe_enabled:
                await self._dopamine_rpe.apply(
                    context=context,
                    current_values={},
                    mutation_service=self._mutation_service,
                )
            # B11 S2: 35-cell category×difficulty learning (separate store).
            if (
                self._difficulty_learner is not None
                and self._mutation_service.config.difficulty_learning_enabled
            ):
                records = await self._difficulty_learner.learn(context)
                # B10: feed the RPE-recent counter with the sign of each applied
                # mutation (real outcome; read-side tally, never the gate).
                if (
                    self._recent_counter is not None
                    and context.session_id
                    and context.category
                ):
                    for record in records:
                        self._recent_counter.record(
                            context.session_id,
                            context.category,
                            record.applied_delta,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=snapshot.trace_id,
                event_type="rpe.pipeline_error",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "phase": "background",
                },
            )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

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

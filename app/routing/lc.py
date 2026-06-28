"""Layer 2 — Locus Coeruleus (sequence step 6).

LC runs after the Semantic Evaluator (step 5 contract) and before the
Tier-1.5 branching decision (step 7 contract). It allocates the
TaskContext and dispatches the PFC notification concurrently — never
serially blocking on PFC.

Phase 3 STEP 3.2: after the TaskContext is allocated, LC consults
Epinephrine to pick a ModelTier and writes the outcome back onto the
context for the executing layer to consume. When no Epinephrine is
injected (legacy Phase 2 unit tests), LC leaves the context's tier
fields at their defaults.
"""
from __future__ import annotations

import asyncio

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import get_spinal_logger
from app.core.model_tier import ModelTier
from app.routing.pfc_stub import notify_pfc
from app.routing.semantic_evaluator import EvaluationResult


class LocusCoeruleus:
    def __init__(self, epinephrine=None, snapshotter=None, lock_manager=None) -> None:
        # Optional injection. `Epinephrine` lives in routing/neuromodulators
        # — kept untyped at this layer to avoid an import cycle if anyone
        # imports LC before the neuromodulators module is built.
        self._epinephrine = epinephrine
        # `SynapseSnapshotter` (app/synapse/snapshot.py). Used by
        # apply_snapshot() — called by routes ONLY after a Tier-1.5 miss,
        # so the snapshot lands solely on the LC-routed path.
        self._snapshotter = snapshotter
        # `LockManager` (app/core/lock_manager.py). Optional — kept untyped
        # to avoid circular imports. Used by force_push() to signal teardown.
        self._lock_manager = lock_manager

    async def apply_snapshot(
        self,
        task_context: TaskContext,
        session_id: str,
        trace_id: str,
    ) -> None:
        """Stamp the current Synapse weight map onto the TaskContext.

        Invoked by routes after the Tier-1.5 branch is confirmed missed,
        so early-exit paths never carry a populated snapshot. No-op when
        no snapshotter is injected (legacy unit-test construction).
        """
        if self._snapshotter is None:
            return
        snapshot = await self._snapshotter.take_snapshot(session_id)
        task_context.synapse_snapshot = snapshot
        await get_spinal_logger().log_event(
            trace_id=trace_id,
            module_name="routing.lc",
            event_type="synapse.snapshot_taken",
            payload={
                "session_id": session_id,
                "category_count": len(snapshot),
            },
        )

    async def process(
        self,
        prompt: str,
        evaluator_result: EvaluationResult,
        trace_id: str | None = None,
    ) -> TaskContext:
        logger = get_spinal_logger()
        # Reuse the caller's trace_id when supplied so the pipeline keeps a
        # single observability anchor end-to-end; fall back to a fresh trace
        # for unit-test callers that exercise LC in isolation.
        if trace_id is None:
            trace_id = await logger.new_trace()

        difficulty = Difficulty(evaluator_result.difficulty)
        # B12: difficulty is the sole tier authority — value-aligned 1:1 with
        # ModelTier (1→LIGHTWEIGHT … 5→DEEP_THINKING). Epinephrine no longer
        # selects the tier (its category→tier map is demoted); it survives below
        # as a high-compute *signal* (epinephrine_active/reason only).
        selected_tier = ModelTier(int(difficulty))
        # Phase 4 STEP 1: ne_boost는 Generator Agent의
        # Norepinephrine.modify_params() 호출의 입력으로 사용됨.
        # B12: 5-stage scale — "high difficulty" is VERY_HARD(4)+ , not HARD(3).
        ne_boost = difficulty >= Difficulty.VERY_HARD

        task_context = TaskContext(
            trace_id=trace_id,
            prompt=prompt,
            category=evaluator_result.category,
            difficulty=difficulty,
            ne_boost=ne_boost,
            selected_tier=selected_tier,
        )

        await logger.log_event(
            trace_id=trace_id,
            module_name="routing.lc",
            event_type="lc.dispatched",
            payload={
                "difficulty": int(difficulty),
                "category": evaluator_result.category,
                "ne_boost": ne_boost,
                "prompt_len": len(prompt),
            },
        )

        # Phase 3 STEP 3.2 / B12 — consult Epinephrine as a high-compute signal
        # only. Its tier suggestion is recorded for observability but NOT applied:
        # difficulty already set selected_tier above. epinephrine_active/reason
        # still reflect the organ's own gate (category + classifier confidence).
        # Skip cleanly when no instance is injected so Phase 2 LC unit tests stay
        # green — selected_tier is already difficulty-derived regardless.
        if self._epinephrine is not None:
            activated, tier_suggestion, reason = await self._epinephrine.decide(
                category=evaluator_result.category,
                similarity=evaluator_result.similarity,
            )
            task_context.epinephrine_active = activated
            task_context.epinephrine_reason = reason
            await logger.log_event(
                trace_id=trace_id,
                module_name="routing.neuromodulators",
                event_type="epinephrine.decided",
                payload={
                    "category": evaluator_result.category,
                    "similarity": evaluator_result.similarity,
                    "activated": activated,
                    # Suggestion only — selected_tier is difficulty-driven (B12).
                    "tier_suggestion": tier_suggestion.name,
                    "selected_tier": task_context.selected_tier.name,
                    "reason": reason,
                },
            )

        # Design contract: LC and PFC run concurrently. Fire-and-forget so
        # LC never blocks on PFC — Phase 5 swaps the stub for the real actor.
        asyncio.create_task(notify_pfc(trace_id, evaluator_result))

        return task_context

    async def force_push(self, trace_id: str) -> None:
        """LC force-push — override PLC for the given trace.

        Marks the trace_id on LockManager so any subsequent acquire() for
        that trace raises CancelledError immediately (Teardown trigger).

        Already-waiting acquires are not preempted; they will expire via
        their configured timeout.  Full task preemption is Phase 5+.

        No-op when no LockManager was injected (legacy unit-test paths).
        """
        if self._lock_manager is None:
            return
        self._lock_manager.mark_force_pushed(trace_id)
        await get_spinal_logger().log_event(
            trace_id=trace_id,
            module_name="routing.lc",
            event_type="lc.force_push",
            payload={"trace_id": trace_id},
        )

"""RPEMutationService: active mutation orchestrator.

Phase 6 STEP 3.1: synapse_weight active mutation.
Phase 6 STEP 4: ifom_ttl dispatch via IFOMTTLMutator (optional).

Service unit only. NO production pipeline integration.
- ActiveMutationConfig.active_enabled is disabled-by-default (B5: the mutation
  gate is active_enabled; observe_enabled gates the upstream observe task only).
- Per-trace-target single-apply (selection, NOT aggregation).
- Internal per-key asyncio.Lock registry — does NOT call app.core.lock_manager
  (its trace+field key shape doesn't fit cross-trace category serialization).
- Manual rollback only. No automatic scheduler.
- current_values from caller are STALE hints; mutator re-reads under lock.
- IFOMTTLMutator is sync; called directly within async context (O(1)).
- Global IFOMConfig is NEVER mutated.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.core.logging import SpinalLogger
from app.rpe.models import (
    ActiveMutationConfig,
    RPEMutationRecord,
    RPEProposal,
)
from app.rpe.mutators import SynapseWeightMutator

if TYPE_CHECKING:
    from app.rpe.mutators import IFOMTTLMutator
    from app.rpe.preset_store import DifficultyPresetStore
    from app.rpe.record_store import RPERecordStore
    from app.rpe.rollback_scheduler import RollbackScheduler


class RPEMutationService:
    MODULE_NAME = "rpe_mutation_service"

    def __init__(
        self,
        mutator: SynapseWeightMutator,
        logger: SpinalLogger,
        config: ActiveMutationConfig | None = None,
        ifom_mutator: "IFOMTTLMutator | None" = None,
        record_store: "RPERecordStore | None" = None,
        preset_store: "DifficultyPresetStore | None" = None,
        rollback_scheduler: "RollbackScheduler | None" = None,
    ) -> None:
        self._mutator = mutator
        self._ifom_mutator = ifom_mutator
        self._logger = logger
        self._config = config if config is not None else ActiveMutationConfig()
        # B3a: optional aiosqlite persistence for applied records (raw, append).
        # None = in-memory only (current behaviour). Persist is a fail-open
        # side-effect; it never alters single-apply selection.
        self._record_store = record_store
        # B3b: optional global EMA preset. Injected ONLY into the cat×difficulty
        # service — a learning mutation rolls its result into the global preset
        # post-apply (learning-only; decay never reaches it). Fail-open.
        self._preset_store = preset_store
        # B4: optional auto-rollback scheduler. An applied mutation is scheduled
        # for timeout rollback unless confirmed; manual rollback() is unchanged.
        self._rollback_scheduler = rollback_scheduler
        # Per-trace-target single-apply registry.
        # key = (trace_id, target_key) → rollback_id of the applied mutation.
        self._applied_in_trace: dict[tuple[str, str], str] = {}
        # rollback_id → RPEMutationRecord.
        self._records: dict[str, RPEMutationRecord] = {}
        # Per-lock_key asyncio.Lock registry (cross-trace category serialization).
        self._key_locks: dict[str, asyncio.Lock] = {}

    @property
    def config(self) -> ActiveMutationConfig:
        return self._config

    def get_record(self, rollback_id: str) -> RPEMutationRecord | None:
        return self._records.get(rollback_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def apply_proposals(
        self,
        proposals: list[RPEProposal],
        current_values: Mapping[str, float] | None = None,
    ) -> list[RPEMutationRecord]:
        # B5: the actual mutation is gated by active_enabled (NOT observe_enabled).
        # observe_enabled=True still routes proposals here (via the observe task),
        # but with active_enabled=False this returns [] and applies nothing.
        if not self._config.active_enabled:
            for proposal in proposals:
                await self._safe_log_event(
                    trace_id=proposal.decision.context.trace_id,
                    event_type="rpe.active_skipped",
                    payload={
                        "source": proposal.decision.reward.source,
                        "target_key": proposal.target_key,
                        "reason": "disabled",
                    },
                )
            return []

        # 1. Threshold qualification.
        qualified: list[RPEProposal] = []
        for proposal in proposals:
            reason = self._threshold_block_reason(proposal)
            if reason is not None:
                await self._safe_log_event(
                    trace_id=proposal.decision.context.trace_id,
                    event_type="rpe.active_blocked",
                    payload={
                        "source": proposal.decision.reward.source,
                        "target_key": proposal.target_key,
                        "reason": reason,
                    },
                )
                continue
            qualified.append(proposal)

        # 2. Group by (trace_id, target_key).
        groups: dict[tuple[str, str], list[RPEProposal]] = {}
        for proposal in qualified:
            key = (proposal.decision.context.trace_id, proposal.target_key)
            groups.setdefault(key, []).append(proposal)

        # 3. For each group, select 1 winner and block the rest.
        records: list[RPEMutationRecord] = []
        for key, group in groups.items():
            trace_id, target_key = key

            # Already applied for this (trace, target) → block all.
            existing_rollback = self._applied_in_trace.get(key)
            if existing_rollback is not None:
                for proposal in group:
                    await self._safe_log_event(
                        trace_id=trace_id,
                        event_type="rpe.active_blocked",
                        payload={
                            "source": proposal.decision.reward.source,
                            "target_key": target_key,
                            "reason": "duplicate_target",
                            "competing_rollback_id": existing_rollback,
                        },
                    )
                continue

            winner = self._select_winner(group)

            # Log blocked losers BEFORE attempting mutation (so even if
            # mutation fails, we have an audit trail of the selection).
            for proposal in group:
                if proposal is winner:
                    continue
                await self._safe_log_event(
                    trace_id=trace_id,
                    event_type="rpe.active_blocked",
                    payload={
                        "source": proposal.decision.reward.source,
                        "target_key": target_key,
                        "reason": "duplicate_target",
                        "competing_rollback_id": winner.rollback_id,
                    },
                )

            record = await self._apply_with_lock(winner, current_values)
            if record is not None:
                records.append(record)
                self._applied_in_trace[key] = record.rollback_id
                self._records[record.rollback_id] = record
                # B3a: persist AFTER the record is finalized — pure side-effect,
                # single-apply selection above is untouched.
                if self._record_store is not None:
                    await self._safe_persist(record)
                # B3b: roll the learned weight into the global EMA preset
                # (learning-only; never decay).
                if self._preset_store is not None:
                    await self._safe_roll_up(record)
                # B4: schedule the tentative mutation for auto-rollback unless
                # confirmed within the timeout (manual rollback unchanged).
                if self._rollback_scheduler is not None:
                    self._rollback_scheduler.schedule(record, self.rollback)

        return records

    async def rollback(self, rollback_id: str) -> RPEMutationRecord | None:
        record = self._records.get(rollback_id)
        if record is None:
            return None
        if record.rollback_status != "available":
            return record

        try:
            if record.proposal.target == "synapse_weight":
                new_record = await self._mutator.rollback(record)
            elif record.proposal.target == "ifom_ttl" and self._ifom_mutator is not None:
                new_record = self._ifom_mutator.rollback(record)
            else:
                return record  # Cannot rollback unknown target
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=record.proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "target_key": record.proposal.target_key,
                    "rollback_id": rollback_id,
                    "phase": "rollback",
                },
            )
            return record

        self._records[rollback_id] = new_record

        await self._safe_log_event(
            trace_id=new_record.proposal.decision.context.trace_id,
            event_type="rpe.active_rollback",
            payload={
                "rollback_id": rollback_id,
                "target_key": new_record.proposal.target_key,
                "previous_value": record.previous_value,
                "current_value_before_rollback": record.new_value,
                "restored_value": new_record.new_value,
                "rolled_back_at": new_record.applied_at,
            },
        )
        # B3a: reflect the new rollback_status in the persisted row.
        if self._record_store is not None:
            await self._safe_update_status(new_record)
        return new_record

    def confirm_mutation(self, rollback_id: str) -> bool:
        """B4: confirm an applied mutation (keep it) by cancelling its pending
        auto-rollback. The surface a confirmation policy (C) calls. No-op (False)
        when no scheduler is wired. Manual rollback() is unaffected."""
        if self._rollback_scheduler is None:
            return False
        return self._rollback_scheduler.confirm(rollback_id)

    # ------------------------------------------------------------------
    # Selection + locking
    # ------------------------------------------------------------------

    def _threshold_block_reason(self, proposal: RPEProposal) -> str | None:
        reward = proposal.decision.reward
        if reward.confidence < self._config.min_confidence:
            return "below_confidence"
        if abs(reward.prediction_error) < self._config.min_abs_prediction_error:
            return "below_prediction_error"
        if proposal.proposed_delta == 0.0:
            return "zero_delta"
        return None

    @staticmethod
    def _select_winner(group: list[RPEProposal]) -> RPEProposal:
        """Select 1 proposal from a (trace, target) group. NOT aggregation."""

        def sort_key(p: RPEProposal) -> tuple[float, int, float, str]:
            return (
                -p.confidence,
                0 if p.decision.reward.source == "mock" else 1,
                -abs(p.proposed_delta),
                p.rollback_id,  # final deterministic tie-breaker
            )

        return sorted(group, key=sort_key)[0]

    def _get_or_create_key_lock(self, lock_key: str) -> asyncio.Lock:
        if lock_key not in self._key_locks:
            self._key_locks[lock_key] = asyncio.Lock()
        return self._key_locks[lock_key]

    async def _apply_with_lock(
        self,
        proposal: RPEProposal,
        current_values: Mapping[str, float] | None,
    ) -> RPEMutationRecord | None:
        # Dispatch to the correct mutator based on proposal.target.
        if proposal.target == "synapse_weight":
            lock_key = f"synapse_weight:{proposal.target_key}"
        elif proposal.target == "ifom_ttl":
            lock_key = f"ifom_ttl:{proposal.target_key}"
            if self._ifom_mutator is None:
                await self._safe_log_event(
                    trace_id=proposal.decision.context.trace_id,
                    event_type="rpe.active_blocked",
                    payload={
                        "source": proposal.decision.reward.source,
                        "target_key": proposal.target_key,
                        "reason": "no_ifom_mutator",
                    },
                )
                return None
        else:
            return None  # Unknown target — skip silently

        lock = self._get_or_create_key_lock(lock_key)
        timeout_s = self._config.lock_timeout_ms / 1000.0

        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout_s)
        except asyncio.TimeoutError:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_blocked",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "reason": "lock_timeout",
                },
            )
            return None
        except asyncio.CancelledError:
            raise

        try:
            session_id = proposal.decision.context.session_id
            if session_id is None:
                await self._safe_log_event(
                    trace_id=proposal.decision.context.trace_id,
                    event_type="rpe.active_error",
                    payload={
                        "source": proposal.decision.reward.source,
                        "target_key": proposal.target_key,
                        "error_type": "ValueError",
                        "error": "session_id is None",
                        "phase": "apply",
                    },
                )
                return None

            if proposal.target == "synapse_weight":
                record = await self._apply_synapse_weight(
                    proposal, session_id, lock_key, current_values
                )
            else:
                # ifom_ttl (ifom_mutator is not None, checked above)
                record = await self._apply_ifom_ttl(
                    proposal, session_id, lock_key, current_values
                )

            if record is not None:
                await self._safe_log_event(
                    trace_id=proposal.decision.context.trace_id,
                    event_type="rpe.active_applied",
                    payload={
                        "session_id": session_id,
                        "source": proposal.decision.reward.source,
                        "target": proposal.target,
                        "target_key": proposal.target_key,
                        "previous_value": record.previous_value,
                        "proposed_delta": proposal.proposed_delta,
                        "applied_delta": record.applied_delta,
                        "new_value": record.new_value,
                        "max_delta": proposal.max_delta,
                        "rollback_id": record.rollback_id,
                        "confidence": proposal.confidence,
                        "prediction_error": proposal.decision.reward.prediction_error,
                        "lock_key": lock_key,
                        "applied_at": record.applied_at,
                        "expires_at": record.expires_at,
                        "current_value_mismatch": record.current_value_mismatch,
                    },
                )
            return record
        finally:
            lock.release()

    async def _apply_synapse_weight(
        self,
        proposal: RPEProposal,
        session_id: str,
        lock_key: str,
        current_values: Mapping[str, float] | None,
    ) -> RPEMutationRecord | None:
        """Apply a synapse_weight proposal under an already-acquired lock."""
        try:
            store_value = await self._mutator.read_current_weight(
                session_id=session_id,
                target_key=proposal.target_key,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "phase": "read",
                },
            )
            return None

        if store_value is None:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "error_type": "MissingWeight",
                    "error": "store returned None for category",
                    "phase": "read",
                },
            )
            return None

        current_value_mismatch = False
        if current_values is not None:
            hint = current_values.get(proposal.target_key)
            if hint is not None and abs(hint - store_value) > 1e-9:
                current_value_mismatch = True

        try:
            return await self._mutator.apply_mutation(
                proposal=proposal,
                previous_value=store_value,
                lock_key=lock_key,
                current_value_mismatch=current_value_mismatch,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "phase": "write",
                },
            )
            return None

    async def _apply_ifom_ttl(
        self,
        proposal: RPEProposal,
        session_id: str,
        lock_key: str,
        current_values: Mapping[str, float] | None,
    ) -> RPEMutationRecord | None:
        """Apply an ifom_ttl proposal under an already-acquired lock.

        IFOMTTLMutator is sync — called directly (O(1) in-memory ops).
        Global IFOMConfig is NEVER mutated.
        """
        # Read current override (None = no override stored yet).
        try:
            store_value = self._ifom_mutator.read_current_ttl(  # type: ignore[union-attr]
                session_id=session_id,
                target_key=proposal.target_key,
            )
        except Exception as exc:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "phase": "read",
                },
            )
            return None

        # If no existing override, fall back to current_values hint.
        if store_value is None:
            if current_values is not None:
                store_value = current_values.get(proposal.target_key)
        if store_value is None:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "error_type": "MissingTTL",
                    "error": "no current TTL value available (store + current_values both None)",
                    "phase": "read",
                },
            )
            return None

        current_value_mismatch = False
        if current_values is not None:
            hint = current_values.get(proposal.target_key)
            if hint is not None and abs(hint - store_value) > 1e-9:
                current_value_mismatch = True

        try:
            return self._ifom_mutator.apply_mutation(  # type: ignore[union-attr]
                proposal=proposal,
                previous_value=store_value,
                lock_key=lock_key,
                current_value_mismatch=current_value_mismatch,
            )
        except Exception as exc:
            await self._safe_log_event(
                trace_id=proposal.decision.context.trace_id,
                event_type="rpe.active_error",
                payload={
                    "source": proposal.decision.reward.source,
                    "target_key": proposal.target_key,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "phase": "write",
                },
            )
            return None

    # ------------------------------------------------------------------
    # Persistence (B3a) — fail-open; durability is an add-on over in-memory
    # ------------------------------------------------------------------

    async def _safe_persist(self, record: RPEMutationRecord) -> None:
        try:
            await self._record_store.persist(record)  # type: ignore[union-attr]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=record.proposal.decision.context.trace_id,
                event_type="rpe.persist_error",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "rollback_id": record.rollback_id,
                    "phase": "persist",
                },
            )

    async def _safe_update_status(self, record: RPEMutationRecord) -> None:
        try:
            await self._record_store.update_status(  # type: ignore[union-attr]
                record.rollback_id, record.rollback_status
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=record.proposal.decision.context.trace_id,
                event_type="rpe.persist_error",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "rollback_id": record.rollback_id,
                    "phase": "update_status",
                },
            )

    async def _safe_roll_up(self, record: RPEMutationRecord) -> None:
        ctx = record.proposal.decision.context
        # Only category×difficulty cells roll up to the global preset; the 7-cell
        # service never gets a preset_store, but guard defensively.
        if not ctx.category or ctx.difficulty < 1:
            return
        try:
            await self._preset_store.update_ema(  # type: ignore[union-attr]
                ctx.category, ctx.difficulty, record.new_value
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id=ctx.trace_id,
                event_type="rpe.preset_error",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "category": ctx.category,
                    "difficulty": ctx.difficulty,
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

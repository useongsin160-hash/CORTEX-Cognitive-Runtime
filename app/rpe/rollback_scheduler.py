"""RPE automatic timeout rollback scheduler (B4).

The last mode-2 safety mechanism. The two safeguards are decay (B11 S5 — gradual
forgetting) and rollback (B4 — immediate revert). An applied mutation is
TENTATIVE: it is scheduled to be auto-rolled-back after a timeout UNLESS it is
explicitly confirmed within the window (revert-unless-confirmed). What counts as
confirmation is a policy C wires; B4 provides only the mechanism, so until then
every mutation auto-reverts — the safe default. Under the B13 freeze nothing
applies, so nothing is scheduled.

Manual rollback is unchanged: this just calls RPEMutationService.rollback()
automatically when the window expires. That rollback restores the record's
previous_value ("undo this mutation"), NOT a time-rewind — so it is independent
of decay, which erodes the in-memory session weight and never the global preset.

Uses apscheduler's AsyncIOScheduler (NOT BackgroundScheduler) so jobs run on the
FastAPI event loop; coroutine job functions are handled by AsyncIOExecutor. The
default in-memory jobstore means pending jobs are lost on restart — the persisted
record (B3a) keeps previous_value for a post-restart manual rollback; this is the
deliberate simplicity boundary (a durable jobstore is out of scope).

⚠️ Distinct from B11 S5 decay (step-based, in-request). This is a wall-clock
scheduler. First real use of the declared apscheduler dependency (A4).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import SpinalLogger
from app.rpe.models import RPEMutationRecord

_DEFAULT_TIMEOUT_S = 300.0

RollbackFn = Callable[[str], Awaitable[Any]]


class RollbackScheduler:
    """Wraps an AsyncIOScheduler to auto-rollback unconfirmed RPE mutations."""

    MODULE_NAME = "rpe_rollback_scheduler"

    def __init__(
        self,
        logger: SpinalLogger,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
        self._logger = logger
        self._timeout_s = timeout_s
        self._scheduler = scheduler if scheduler is not None else AsyncIOScheduler()
        # rollback_ids with a pending auto-rollback job.
        self._pending: set[str] = set()

    # ----- lifecycle -----

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    @property
    def pending(self) -> set[str]:
        return set(self._pending)

    # ----- schedule / confirm / fire -----

    def schedule(self, record: RPEMutationRecord, rollback_fn: RollbackFn) -> None:
        """Register an auto-rollback for a freshly applied mutation. Fires at
        now + timeout unless confirm() cancels it first. rollback_fn is the
        service's bound rollback() — passed in so this module never imports the
        service (no cycle)."""
        rollback_id = record.rollback_id
        trace_id = record.proposal.decision.context.trace_id
        run_date = datetime.now(timezone.utc) + timedelta(seconds=self._timeout_s)
        self._scheduler.add_job(
            self._fire,
            "date",
            run_date=run_date,
            args=[rollback_id, trace_id, rollback_fn],
            id=rollback_id,
            replace_existing=True,
        )
        self._pending.add(rollback_id)

    def confirm(self, rollback_id: str) -> bool:
        """Confirm a mutation (keep it): cancel its pending auto-rollback.
        Returns True iff a pending job was actually cancelled."""
        if rollback_id not in self._pending:
            return False
        self._pending.discard(rollback_id)
        try:
            self._scheduler.remove_job(rollback_id)
        except JobLookupError:
            return False
        return True

    async def _fire(
        self, rollback_id: str, trace_id: str, rollback_fn: RollbackFn
    ) -> None:
        """Job body: the window expired without confirmation → auto-rollback via
        the service's manual rollback (restores previous_value)."""
        self._pending.discard(rollback_id)
        try:
            await rollback_fn(rollback_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._safe_log_event(
                trace_id,
                "rpe.auto_rollback_error",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "rollback_id": rollback_id,
                },
            )
            return
        await self._safe_log_event(
            trace_id,
            "rpe.auto_rollback",
            {"rollback_id": rollback_id, "reason": "timeout_unconfirmed"},
        )

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

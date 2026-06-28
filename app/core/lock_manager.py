"""Field-level async lock manager — Phase 4 STEP 4.

Design rules (v0.4 12.2):
- field-level short lock only; TaskContext-wide lock forbidden
- Trace_ID + field_name key → per-request, per-field granularity
- LockType controls timeout budget
- CancelledError is NEVER swallowed — always re-raised (STEP 3.2 pattern)
- force_push marks a trace_id so the next acquire raises CancelledError
  (already-waiting acquires expire via timeout; full preemption is Phase 5+)
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Final

from app.core.errors import LockTimeoutError


class LockType(Enum):
    READ = "read"
    FIELD_UPDATE = "field_update"
    CHROMADB_WRITE = "chromadb_write"


LOCK_TIMEOUTS: Final[dict[LockType, float]] = {
    LockType.READ: 1.0,
    LockType.FIELD_UPDATE: 1.0,
    LockType.CHROMADB_WRITE: 3.0,
}


@dataclass
class LockState:
    """Metadata for a single managed lock."""

    lock_type: LockType
    trace_id: str
    acquired_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LockManager:
    """Trace_ID + field_name keyed async lock manager.

    Singleton in app.state; TaskContext must never hold a reference to
    this object (v0.4 rule: no runtime objects inside TaskContext).

    Usage::

        async with lock_manager.acquire(trace_id, "context_agent_result",
                                        LockType.FIELD_UPDATE):
            task_context.context_agent_result = result
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._states: dict[str, LockState] = {}
        # Trace IDs marked for force-push by LC.force_push().
        # Any new acquire() for these trace IDs raises CancelledError.
        self._force_pushed: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire(
        self,
        trace_id: str,
        field_name: str,
        lock_type: LockType,
    ) -> AsyncIterator[None]:
        """Acquire a field-level lock for the given trace.

        Raises:
            LockTimeoutError: timeout reached before lock was granted.
            asyncio.CancelledError: caller task was cancelled, or LC
                called force_push() for this trace_id.
        """
        if trace_id in self._force_pushed:
            raise asyncio.CancelledError(
                f"LC force_push active for trace_id={trace_id}"
            )

        lock_key = f"{trace_id}:{field_name}"
        timeout = LOCK_TIMEOUTS[lock_type]
        lock = self._get_or_create_lock(lock_key)

        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            raise LockTimeoutError(trace_id, field_name, lock_type.value, timeout)

        self._states[lock_key] = LockState(
            lock_type=lock_type,
            trace_id=trace_id,
            acquired_at=time.monotonic(),
        )
        try:
            yield
        except asyncio.CancelledError:
            raise
        finally:
            lock.release()
            self._states.pop(lock_key, None)

    def mark_force_pushed(self, trace_id: str) -> None:
        """Mark trace_id for LC force-push.

        Subsequent acquire() calls for this trace_id raise CancelledError.
        """
        self._force_pushed.add(trace_id)

    def clear_force_push(self, trace_id: str) -> None:
        """Remove the force-push mark (cleanup after teardown)."""
        self._force_pushed.discard(trace_id)

    def is_force_pushed(self, trace_id: str) -> bool:
        return trace_id in self._force_pushed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_lock(self, lock_key: str) -> asyncio.Lock:
        if lock_key not in self._locks:
            self._locks[lock_key] = asyncio.Lock()
        return self._locks[lock_key]

"""Pipeline Lock Coordinator — Phase 4 STEP 4.

PLC wraps LockManager with semantic method names for the three
protected areas defined in v0.4 §12.2:

  1. TaskContext.context_agent_result update
  2. ChromaDB write operations
  3. synapse_snapshot update

LC force-push (override) is handled via LockManager.mark_force_pushed().
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.lock_manager import LockManager, LockType


class PLC:
    """Pipeline Lock Coordinator.

    Injected into AsyncSwarm via factory.  LC holds a reference for
    force_push() override.  TaskContext must never hold a PLC instance.
    """

    def __init__(self, lock_manager: LockManager) -> None:
        self._lock_manager = lock_manager

    # ------------------------------------------------------------------
    # Protected regions (v0.4 §12.2)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def protect_context_update(self, trace_id: str) -> AsyncIterator[None]:
        """Protect TaskContext.context_agent_result write.

        Uses FIELD_UPDATE lock (timeout 1 s).
        CancelledError is never swallowed.
        """
        async with self._lock_manager.acquire(
            trace_id, "context_agent_result", LockType.FIELD_UPDATE
        ):
            yield

    @asynccontextmanager
    async def protect_chromadb_write(self, trace_id: str) -> AsyncIterator[None]:
        """Protect ChromaDB write operations.

        Uses CHROMADB_WRITE lock (timeout 3 s).
        """
        async with self._lock_manager.acquire(
            trace_id, "chromadb_write", LockType.CHROMADB_WRITE
        ):
            yield

    @asynccontextmanager
    async def protect_synapse_update(self, trace_id: str) -> AsyncIterator[None]:
        """Protect synapse_snapshot update moment.

        Uses FIELD_UPDATE lock (timeout 1 s).
        """
        async with self._lock_manager.acquire(
            trace_id, "synapse_snapshot", LockType.FIELD_UPDATE
        ):
            yield

    # ------------------------------------------------------------------
    # Force-push passthrough (delegated to LockManager)
    # ------------------------------------------------------------------

    def mark_force_pushed(self, trace_id: str) -> None:
        self._lock_manager.mark_force_pushed(trace_id)

    def clear_force_push(self, trace_id: str) -> None:
        self._lock_manager.clear_force_push(trace_id)

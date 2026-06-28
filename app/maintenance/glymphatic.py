"""GlymphaticCleaner — periodic age-based cleanup of persistent stores (B9).

The glymphatic system is the brain's waste-clearance pathway that flushes
metabolic by-products during sleep. CORTEX's GlymphaticCleaner is its analogue:
a periodic background pass that removes *aged* entries from the persistent stores
nothing else cleans.

Role boundary (no overlap — the B2 lesson):
  - IFOM TTL          → forgets PFC *goals* (in-memory goal stack), by status TTL.
  - decay (B11 S5)    → erodes RPE 35-cell routing *weights*.
  - rollback (B4)     → undoes one bad RPE *mutation*.
  - ratchet/goal_stack→ self-bounded LRU.
  - GlymphaticCleaner → ages out *persistent stores* (the ChromaDB semantic cache
                        and the aiosqlite RPE record table) that have no eviction.

This module is a leaf: it imports NOTHING from app.rpe / app.routing / app.ingress.
Cleanup targets are injected as a duck-typed ``AgeCleanableStore`` protocol, so the
cleaner never reaches into the learning/routing layers (enforced by an AST
isolation test). It makes ZERO LLM calls — cleanup is pure deletion.

Strategy seam: ``CleanupStrategy`` lets the *action* be swapped. Only
``DeleteStrategy`` (pure delete) is implemented and registered. A future
``CompressArchiveStrategy`` (LLM summarize-and-archive = memory consolidation, an
OPERA v1.1 / "기억 통합" direction) would plug in here — it is intentionally NOT
implemented (no LLM in maintenance, no NotImplementedError stub).
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.logging import SpinalLogger

# Synthetic trace_id for cleanup-cycle telemetry. The cycle is not request-bound,
# so it owns a constant trace that also keys any injected PLC lock.
_TRACE_ID = "glymphatic"


@runtime_checkable
class AgeCleanableStore(Protocol):
    """A persistent store that can delete entries older than an age cutoff.

    ``now`` is wall-clock epoch seconds; the store derives its own cutoff
    (``now - max_age_s``) in whatever timestamp format it persists. Returns the
    number of entries deleted (bounded by ``batch_limit``)."""

    async def delete_older_than(
        self, now: float, max_age_s: float, batch_limit: int
    ) -> int: ...


@dataclass(frozen=True)
class CleanupTarget:
    """One store to clean, with its per-store age budget and optional lock.

    ``lock_factory`` (when provided) yields an async context manager held around
    the delete — e.g. the ChromaDB target wraps deletion in PLC's
    ``protect_chromadb_write`` so the cleaner never imports PLC itself.
    """

    name: str
    store: AgeCleanableStore
    max_age_s: float
    lock_factory: Callable[[], AbstractAsyncContextManager] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"name must be a non-empty str, got {self.name!r}")
        if self.max_age_s <= 0:
            raise ValueError(f"max_age_s must be > 0, got {self.max_age_s}")


class CleanupStrategy(ABC):
    """The *action* applied to aged entries. Swappable (delete vs. future
    compress-archive). ``name`` labels it for telemetry."""

    name: str

    @abstractmethod
    async def clean(
        self, target: CleanupTarget, *, now: float, batch_limit: int
    ) -> int:
        """Apply this strategy to one target. Returns entries affected."""
        raise NotImplementedError  # pragma: no cover - abstract


class DeleteStrategy(CleanupStrategy):
    """Pure deletion of aged entries — no LLM, no archive. The glymphatic
    metaphor's literal "flush": aged entries are removed, not summarized."""

    name = "delete"

    async def clean(
        self, target: CleanupTarget, *, now: float, batch_limit: int
    ) -> int:
        return await target.store.delete_older_than(
            now, target.max_age_s, batch_limit
        )


# Strategy registry. Only "delete" is implemented. A future
# "compress_archive" (LLM summarize → archive collection) would be registered
# here; it is deliberately absent — maintenance stays no-LLM in B9.
STRATEGIES: dict[str, type[CleanupStrategy]] = {DeleteStrategy.name: DeleteStrategy}


class GlymphaticCleaner:
    """Periodic age-based cleaner over a fixed set of injected targets.

    One ``run_cycle()`` is driven on an interval by the shared AsyncIOScheduler
    (wired in app.main). The whole pass is fail-open: a failure on one target is
    logged and the cycle moves on; ``asyncio.CancelledError`` is always re-raised.
    A destructive op, so it is gated by ``enabled`` (off → no-op) and bounded by
    ``batch_limit`` (a single cycle can delete at most that many per target).
    """

    MODULE_NAME = "glymphatic"

    def __init__(
        self,
        targets: Sequence[CleanupTarget],
        strategy: CleanupStrategy,
        logger: SpinalLogger,
        *,
        enabled: bool,
        batch_limit: int,
    ) -> None:
        if batch_limit <= 0:
            raise ValueError(f"batch_limit must be > 0, got {batch_limit}")
        self._targets = tuple(targets)
        self._strategy = strategy
        self._logger = logger
        self._enabled = enabled
        self._batch_limit = batch_limit

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def run_cycle(self) -> None:
        """Clean every target once. No-op when disabled (the hygiene safety
        valve — deletion never fires unless explicitly enabled)."""
        if not self._enabled:
            return
        now = time.time()
        total_deleted = 0
        for target in self._targets:
            try:
                lock_cm = (
                    target.lock_factory()
                    if target.lock_factory is not None
                    else nullcontext()
                )
                async with lock_cm:
                    deleted = await self._strategy.clean(
                        target, now=now, batch_limit=self._batch_limit
                    )
                total_deleted += deleted
                await self._safe_log(
                    "glymphatic.target_cleaned",
                    {
                        "target": target.name,
                        "strategy": self._strategy.name,
                        "deleted": deleted,
                        "max_age_s": target.max_age_s,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Fail-open: one target's failure must not abort the cycle or
                # crash the scheduler.
                await self._safe_log(
                    "glymphatic.target_error",
                    {
                        "target": target.name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                continue
        await self._safe_log(
            "glymphatic.cycle",
            {"targets": len(self._targets), "total_deleted": total_deleted},
        )

    async def _safe_log(self, event_type: str, payload: dict) -> None:
        try:
            await self._logger.log_event(
                trace_id=_TRACE_ID,
                module_name=self.MODULE_NAME,
                event_type=event_type,
                payload=payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

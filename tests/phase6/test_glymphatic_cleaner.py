"""B9 — GlymphaticCleaner orchestration tests (fake stores, no ChromaDB/e5).

Covers: cleans every target with the right age/batch, disabled is a no-op,
fail-open (one target's error never aborts the cycle), CancelledError is
re-raised, the lock_factory wraps the delete, target validation, and that only
the delete strategy is registered (compress-archive is absent, not stubbed).
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.logging import SpinalLogger
from app.maintenance.glymphatic import (
    STRATEGIES,
    CleanupTarget,
    DeleteStrategy,
    GlymphaticCleaner,
)


class _FakeStore:
    """AgeCleanableStore double — records calls, optionally raises."""

    def __init__(self, *, deleted: int = 0, raise_exc: BaseException | None = None):
        self.calls: list[tuple[float, float, int]] = []
        self._deleted = deleted
        self._raise = raise_exc

    async def delete_older_than(
        self, now: float, max_age_s: float, batch_limit: int
    ) -> int:
        self.calls.append((now, max_age_s, batch_limit))
        if self._raise is not None:
            raise self._raise
        return self._deleted


class _FakeLock:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> "_FakeLock":
        self.entered += 1
        return self

    async def __aexit__(self, *exc) -> bool:
        self.exited += 1
        return False


def _cleaner(targets, *, enabled=True, batch_limit=100):
    return GlymphaticCleaner(
        targets,
        DeleteStrategy(),
        SpinalLogger(),
        enabled=enabled,
        batch_limit=batch_limit,
    )


@pytest.mark.asyncio
async def test_cleans_all_targets_with_age_and_batch():
    a, b = _FakeStore(deleted=3), _FakeStore(deleted=2)
    cleaner = _cleaner(
        (
            CleanupTarget(name="a", store=a, max_age_s=10.0),
            CleanupTarget(name="b", store=b, max_age_s=20.0),
        ),
        batch_limit=50,
    )
    await cleaner.run_cycle()
    assert len(a.calls) == 1 and len(b.calls) == 1
    # (now, max_age_s, batch_limit) — age is per-target, batch is global.
    assert a.calls[0][1] == 10.0 and a.calls[0][2] == 50
    assert b.calls[0][1] == 20.0 and b.calls[0][2] == 50


@pytest.mark.asyncio
async def test_disabled_is_noop():
    a = _FakeStore(deleted=5)
    cleaner = _cleaner(
        (CleanupTarget(name="a", store=a, max_age_s=10.0),), enabled=False
    )
    await cleaner.run_cycle()
    assert a.calls == []


@pytest.mark.asyncio
async def test_fail_open_one_target_does_not_abort_cycle():
    bad = _FakeStore(raise_exc=RuntimeError("boom"))
    good = _FakeStore(deleted=1)
    cleaner = _cleaner(
        (
            CleanupTarget(name="bad", store=bad, max_age_s=10.0),
            CleanupTarget(name="good", store=good, max_age_s=10.0),
        )
    )
    await cleaner.run_cycle()  # must not raise
    assert len(bad.calls) == 1
    assert len(good.calls) == 1  # reached despite bad's failure


@pytest.mark.asyncio
async def test_cancelled_error_is_reraised():
    a = _FakeStore(raise_exc=asyncio.CancelledError())
    cleaner = _cleaner((CleanupTarget(name="a", store=a, max_age_s=10.0),))
    with pytest.raises(asyncio.CancelledError):
        await cleaner.run_cycle()


@pytest.mark.asyncio
async def test_lock_factory_wraps_delete():
    lock = _FakeLock()
    store = _FakeStore(deleted=1)
    cleaner = _cleaner(
        (
            CleanupTarget(
                name="locked",
                store=store,
                max_age_s=10.0,
                lock_factory=lambda: lock,
            ),
        )
    )
    await cleaner.run_cycle()
    assert lock.entered == 1 and lock.exited == 1
    assert len(store.calls) == 1


@pytest.mark.asyncio
async def test_no_lock_factory_still_cleans():
    store = _FakeStore(deleted=1)
    cleaner = _cleaner((CleanupTarget(name="x", store=store, max_age_s=10.0),))
    await cleaner.run_cycle()
    assert len(store.calls) == 1


def test_only_delete_strategy_registered():
    assert "delete" in STRATEGIES
    assert "compress_archive" not in STRATEGIES
    assert "compress" not in STRATEGIES


def test_cleanup_target_validation():
    store = _FakeStore()
    with pytest.raises(ValueError, match="max_age_s"):
        CleanupTarget(name="a", store=store, max_age_s=0.0)
    with pytest.raises(ValueError, match="name"):
        CleanupTarget(name="", store=store, max_age_s=10.0)


def test_cleaner_rejects_nonpositive_batch():
    with pytest.raises(ValueError, match="batch_limit"):
        GlymphaticCleaner(
            (), DeleteStrategy(), SpinalLogger(), enabled=True, batch_limit=0
        )

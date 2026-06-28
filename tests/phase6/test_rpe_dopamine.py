"""Phase 6 STEP 1 — DopamineRPE observe-only tests."""

from __future__ import annotations

import asyncio

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import RPEContext, RPEReward
from app.rpe.sources import MockRewardSource


def _ctx(trace_id: str) -> RPEContext:
    return RPEContext(trace_id=trace_id, response_source="generated")


class _RaisingSource:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def compute_reward(self, context: RPEContext) -> RPEReward:
        raise self._exc


class _RaisingLogger:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def log_event(self, **kwargs) -> None:
        raise self._exc


class TestConstruction:
    def test_observe_only_default(self) -> None:
        rpe = DopamineRPE(sources=[], logger=SpinalLogger())
        assert rpe.mode == "observe_only"

    def test_dry_run_rejected(self) -> None:
        with pytest.raises(ValueError, match="observe_only"):
            DopamineRPE(sources=[], logger=SpinalLogger(), mode="dry_run")  # type: ignore[arg-type]

    def test_active_rejected(self) -> None:
        with pytest.raises(ValueError, match="observe_only"):
            DopamineRPE(sources=[], logger=SpinalLogger(), mode="active")  # type: ignore[arg-type]


class TestObserveOnly:
    @pytest.mark.asyncio
    async def test_empty_sources_returns_empty_list(self) -> None:
        rpe = DopamineRPE(sources=[], logger=SpinalLogger())
        await SpinalLogger().new_trace()
        decisions = await rpe.observe(_ctx("trace-empty"))
        assert decisions == []

    @pytest.mark.asyncio
    async def test_single_source(self) -> None:
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=SpinalLogger())
        decisions = await rpe.observe(_ctx("trace-single"))
        assert len(decisions) == 1
        d = decisions[0]
        assert d.mode == "observe_only"
        assert d.applied is False
        assert d.target is None
        assert d.proposed_delta is None
        assert d.rollback_id is None
        assert d.reward.source == "mock"

    @pytest.mark.asyncio
    async def test_multiple_sources(self) -> None:
        rpe = DopamineRPE(
            sources=[
                MockRewardSource(reward_map={"trace-m": (0.5, 0.7)}),
                MockRewardSource(reward_map={"trace-m": (0.2, 0.4)}),
            ],
            logger=SpinalLogger(),
        )
        decisions = await rpe.observe(_ctx("trace-m"))
        assert len(decisions) == 2
        assert all(d.mode == "observe_only" for d in decisions)
        assert all(d.applied is False for d in decisions)
        assert all(d.target is None for d in decisions)

    @pytest.mark.asyncio
    async def test_source_failure_logs_and_continues(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(
            sources=[
                _RaisingSource(RuntimeError("boom")),
                MockRewardSource(),
            ],
            logger=logger,
        )
        decisions = await rpe.observe(_ctx("trace-fail"))
        assert len(decisions) == 1
        assert decisions[0].reward.source == "mock"
        events = logger.get_trace("trace-fail")
        event_types = [e.event_type for e in events]
        assert "rpe.source_error" in event_types
        assert "rpe.observed" in event_types

    @pytest.mark.asyncio
    async def test_source_cancelled_reraises(self) -> None:
        rpe = DopamineRPE(
            sources=[_RaisingSource(asyncio.CancelledError())],
            logger=SpinalLogger(),
        )
        with pytest.raises(asyncio.CancelledError):
            await rpe.observe(_ctx("trace-cancel"))

    @pytest.mark.asyncio
    async def test_logger_failure_does_not_break_observe(self) -> None:
        rpe = DopamineRPE(
            sources=[MockRewardSource()],
            logger=_RaisingLogger(RuntimeError("log failure")),  # type: ignore[arg-type]
        )
        decisions = await rpe.observe(_ctx("trace-logger-fail"))
        assert len(decisions) == 1
        assert decisions[0].reward.source == "mock"

    @pytest.mark.asyncio
    async def test_logger_cancelled_reraises(self) -> None:
        rpe = DopamineRPE(
            sources=[MockRewardSource()],
            logger=_RaisingLogger(asyncio.CancelledError()),  # type: ignore[arg-type]
        )
        with pytest.raises(asyncio.CancelledError):
            await rpe.observe(_ctx("trace-logger-cancel"))

    @pytest.mark.asyncio
    async def test_no_mutation_invariants_hold(self) -> None:
        rpe = DopamineRPE(
            sources=[MockRewardSource(), MockRewardSource()],
            logger=SpinalLogger(),
        )
        decisions = await rpe.observe(_ctx("trace-invariant"))
        for d in decisions:
            assert d.mode == "observe_only"
            assert d.applied is False
            assert d.target is None
            assert d.proposed_delta is None
            assert d.rollback_id is None

"""Phase 6 STEP 2 — dry-run event logging tests."""

from __future__ import annotations

import asyncio

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import RPEContext, RPEReward
from app.rpe.sources import MockRewardSource


def _ctx(trace_id: str, category: str | None = "coding", **kw) -> RPEContext:
    return RPEContext(trace_id=trace_id, category=category, **kw)


class _RaisingSource:
    async def compute_reward(self, context: RPEContext) -> RPEReward:
        raise RuntimeError("source boom")


class _RaisingLogger:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def log_event(self, **_kwargs) -> None:
        raise self._exc


class TestProposedEvent:
    @pytest.mark.asyncio
    async def test_proposed_payload_keys(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(
            sources=[MockRewardSource(reward_map={"trace-dl-1": (0.3, 0.9)})],
            logger=logger,
        )
        await rpe.dry_run(_ctx("trace-dl-1"), current_values={"category:coding": 0.5})
        events = [
            e for e in logger.get_trace("trace-dl-1")
            if e.event_type == "rpe.dry_run_proposed"
        ]
        assert len(events) == 1
        e = events[0]
        assert e.module_name == "dopamine_rpe"
        for key in (
            "source",
            "target",
            "target_key",
            "current_value",
            "proposed_delta",
            "proposed_value",
            "max_delta",
            "rollback_id",
            "confidence",
            "prediction_error",
            "category",
            "applied",
        ):
            assert key in e.payload, key
        assert e.payload["target"] == "synapse_weight"
        assert e.payload["applied"] is False
        assert e.payload["category"] == "coding"

    @pytest.mark.asyncio
    async def test_trace_id_in_event(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        await rpe.dry_run(_ctx("trace-dl-tid"))
        events = logger.get_trace("trace-dl-tid")
        assert any(e.trace_id == "trace-dl-tid" for e in events)


class TestSkippedEvent:
    @pytest.mark.asyncio
    async def test_no_category_skipped_reason(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        await rpe.dry_run(_ctx("trace-skip-nc", category=None))
        events = [
            e for e in logger.get_trace("trace-skip-nc")
            if e.event_type == "rpe.dry_run_skipped"
        ]
        assert len(events) == 1
        assert events[0].payload["reason"] == "no_category"

    @pytest.mark.asyncio
    async def test_invalid_category_skipped_reason(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        await rpe.dry_run(_ctx("trace-skip-ic", category="quantum_cooking"))
        events = [
            e for e in logger.get_trace("trace-skip-ic")
            if e.event_type == "rpe.dry_run_skipped"
        ]
        assert len(events) == 1
        assert events[0].payload["reason"] == "invalid_category"

    @pytest.mark.asyncio
    async def test_invalid_current_value_skipped_reason(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        # current_value=0.05 → below weight_min=0.1 → skip
        await rpe.dry_run(
            _ctx("trace-skip-icv"),
            current_values={"category:coding": 0.05},
        )
        events = [
            e for e in logger.get_trace("trace-skip-icv")
            if e.event_type == "rpe.dry_run_skipped"
        ]
        assert len(events) == 1
        assert events[0].payload["reason"] == "invalid_current_value"

    @pytest.mark.asyncio
    async def test_skipped_event_has_target(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        await rpe.dry_run(_ctx("trace-skip-t", category=None))
        events = [
            e for e in logger.get_trace("trace-skip-t")
            if e.event_type == "rpe.dry_run_skipped"
        ]
        assert events[0].payload["target"] == "synapse_weight"


class TestErrorEvent:
    @pytest.mark.asyncio
    async def test_source_error_logs_dry_run_error(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(
            sources=[_RaisingSource(), MockRewardSource()],
            logger=logger,
        )
        proposals = await rpe.dry_run(_ctx("trace-err-1"))
        source_err = [
            e for e in logger.get_trace("trace-err-1")
            if e.event_type == "rpe.source_error"
        ]
        assert len(source_err) == 1
        # The non-raising source still produces a proposal.
        assert len(proposals) == 1


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_logger_failure_does_not_break_dry_run(self) -> None:
        rpe = DopamineRPE(
            sources=[MockRewardSource()],
            logger=_RaisingLogger(RuntimeError("log fail")),  # type: ignore[arg-type]
        )
        proposals = await rpe.dry_run(_ctx("trace-fo-1"))
        assert len(proposals) == 1

    @pytest.mark.asyncio
    async def test_logger_cancelled_reraises_in_dry_run(self) -> None:
        rpe = DopamineRPE(
            sources=[MockRewardSource()],
            logger=_RaisingLogger(asyncio.CancelledError()),  # type: ignore[arg-type]
        )
        with pytest.raises(asyncio.CancelledError):
            await rpe.dry_run(_ctx("trace-fo-cancel"))

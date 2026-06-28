"""Phase 6 STEP 1 — DopamineRPE event logging tests."""

from __future__ import annotations

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import RPEContext, RPEReward
from app.rpe.sources import MockRewardSource


def _ctx(trace_id: str, **overrides) -> RPEContext:
    defaults = {
        "trace_id": trace_id,
        "category": "qa",
        "response_source": "generated",
        "pfc_active": True,
        "continuation_bypass": False,
    }
    defaults.update(overrides)
    return RPEContext(**defaults)


class _RaisingSource:
    async def compute_reward(self, context: RPEContext) -> RPEReward:
        raise RuntimeError("source explosion")


class TestObservedEvent:
    @pytest.mark.asyncio
    async def test_payload_contains_required_fields(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(
            sources=[MockRewardSource(reward_map={"trace-log-1": (0.3, 0.8)})],
            logger=logger,
        )
        await rpe.observe(_ctx("trace-log-1"))
        events = [
            e for e in logger.get_trace("trace-log-1") if e.event_type == "rpe.observed"
        ]
        assert len(events) == 1
        e = events[0]
        assert e.module_name == "dopamine_rpe"
        assert e.trace_id == "trace-log-1"
        for key in (
            "source",
            "expected_reward",
            "actual_reward",
            "prediction_error",
            "confidence",
            "category",
            "response_source",
            "pfc_active",
            "continuation_bypass",
        ):
            assert key in e.payload, key
        assert e.payload["source"] == "mock"
        assert e.payload["expected_reward"] == 0.3
        assert e.payload["actual_reward"] == 0.8
        assert e.payload["prediction_error"] == pytest.approx(0.5)
        assert e.payload["confidence"] == 1.0
        assert e.payload["category"] == "qa"
        assert e.payload["response_source"] == "generated"
        assert e.payload["pfc_active"] is True
        assert e.payload["continuation_bypass"] is False


class TestSourceErrorEvent:
    @pytest.mark.asyncio
    async def test_payload_contains_error_metadata(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[_RaisingSource()], logger=logger)
        await rpe.observe(_ctx("trace-log-err"))
        events = [
            e
            for e in logger.get_trace("trace-log-err")
            if e.event_type == "rpe.source_error"
        ]
        assert len(events) == 1
        e = events[0]
        assert e.module_name == "dopamine_rpe"
        assert e.trace_id == "trace-log-err"
        assert e.payload["source_class"] == "_RaisingSource"
        assert e.payload["error_type"] == "RuntimeError"
        assert "source explosion" in e.payload["error"]

    @pytest.mark.asyncio
    async def test_no_observed_event_on_source_error(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[_RaisingSource()], logger=logger)
        await rpe.observe(_ctx("trace-log-err-2"))
        observed = [
            e
            for e in logger.get_trace("trace-log-err-2")
            if e.event_type == "rpe.observed"
        ]
        assert observed == []

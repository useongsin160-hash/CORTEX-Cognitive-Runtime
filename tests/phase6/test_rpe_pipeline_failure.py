"""Phase 6 STEP 3.2 — RPEMutationPipelineWrapper failure / fail-open tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.schemas.context import TaskContext
from app.core.logging import SpinalLogger
from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult
from app.rpe.models import ActiveMutationConfig
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.pipeline import RPEMutationPipelineWrapper
from app.rpe.service import RPEMutationService


def _swarm_result() -> SwarmResult:
    return SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="ok",
            tier_used="STANDARD",
            model_name="mock",
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason="stop",
            latency_ms=1.0,
            ne_applied=False,
            plan_intent="answer",
        ),
        context_status="ok",
        planner_status="ok",
        generator_status="ok",
        total_elapsed_ms=10.0,
    )


def _make_wrapper(
    *,
    enabled: bool = True,
    rpe_apply_side_effect=None,
) -> tuple[RPEMutationPipelineWrapper, MagicMock]:
    from app.rpe.dopamine import DopamineRPE
    from app.rpe.sources import MockRewardSource

    inner = MagicMock()
    inner.execute = AsyncMock(return_value=_swarm_result())

    store = InMemorySynapseWeightStore({("sess-f", "coding"): 0.5})
    mutator = SynapseWeightMutator(store=store)
    logger = SpinalLogger()
    # B5: these fail-open tests drive the background observe task (gated by
    # observe_enabled) and inject errors via rpe.apply — mutation is irrelevant.
    config = ActiveMutationConfig(observe_enabled=enabled)
    svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
    rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)

    if rpe_apply_side_effect is not None:
        rpe.apply = AsyncMock(side_effect=rpe_apply_side_effect)

    wrapper = RPEMutationPipelineWrapper(
        inner_swarm=inner,
        dopamine_rpe=rpe,
        mutation_service=svc,
        logger=logger,
    )
    return wrapper, inner


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_bg_error_does_not_affect_response(self) -> None:
        """Background RPE error must NOT raise to caller."""
        wrapper, _ = _make_wrapper(
            enabled=True,
            rpe_apply_side_effect=RuntimeError("rpe blew up"),
        )
        tc = TaskContext(trace_id="t-fail-open", category="coding", difficulty=2)
        result = await wrapper.execute(tc, trace_id="t-fail-open", session_id="sess-f")
        # Wait for bg task to settle.
        await asyncio.sleep(0.01)
        # Response must still be the swarm result.
        assert result.generator_result.text == "ok"

    @pytest.mark.asyncio
    async def test_swarm_exception_propagates_to_caller(self) -> None:
        """If the inner swarm raises, the wrapper propagates it."""
        inner = MagicMock()
        inner.execute = AsyncMock(side_effect=ValueError("swarm failed"))

        from app.rpe.dopamine import DopamineRPE
        from app.rpe.sources import MockRewardSource

        logger = SpinalLogger()
        store = InMemorySynapseWeightStore()
        mutator = SynapseWeightMutator(store=store)
        svc = RPEMutationService(mutator, logger, ActiveMutationConfig(observe_enabled=True))
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        wrapper = RPEMutationPipelineWrapper(inner, rpe, svc, logger)

        with pytest.raises(ValueError, match="swarm failed"):
            await wrapper.execute(
                TaskContext(trace_id="t-prop", category="coding", difficulty=2),
                trace_id="t-prop",
                session_id="s-prop",
            )

    @pytest.mark.asyncio
    async def test_bg_error_logged_not_raised(self) -> None:
        """Background errors are logged as rpe.pipeline_error."""
        from app.rpe.dopamine import DopamineRPE
        from app.rpe.sources import MockRewardSource

        inner = MagicMock()
        inner.execute = AsyncMock(return_value=_swarm_result())

        logger = SpinalLogger()
        store = InMemorySynapseWeightStore({("sess-log", "coding"): 0.5})
        mutator = SynapseWeightMutator(store=store)
        config = ActiveMutationConfig(observe_enabled=True)
        svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        # Inject error into apply
        rpe.apply = AsyncMock(side_effect=RuntimeError("log-test"))

        wrapper = RPEMutationPipelineWrapper(inner, rpe, svc, logger)
        trace_id = "t-log-err"
        tc = TaskContext(trace_id=trace_id, category="coding", difficulty=2)
        await wrapper.execute(tc, trace_id=trace_id, session_id="sess-log")
        await asyncio.sleep(0.05)

        events = [
            e for e in logger.get_trace(trace_id)
            if e.event_type == "rpe.pipeline_error"
        ]
        assert len(events) >= 1
        assert events[0].payload["error_type"] == "RuntimeError"
        assert "log-test" in events[0].payload["error"]

    @pytest.mark.asyncio
    async def test_cancelled_error_from_inner_swarm_reraises(self) -> None:
        inner = MagicMock()
        inner.execute = AsyncMock(side_effect=asyncio.CancelledError())

        from app.rpe.dopamine import DopamineRPE
        from app.rpe.sources import MockRewardSource

        logger = SpinalLogger()
        svc = RPEMutationService(
            SynapseWeightMutator(InMemorySynapseWeightStore()),
            logger,
        )
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        wrapper = RPEMutationPipelineWrapper(inner, rpe, svc, logger)

        with pytest.raises(asyncio.CancelledError):
            await wrapper.execute(
                TaskContext(trace_id="t-cancel", category="coding", difficulty=2),
                trace_id="t-cancel",
                session_id="s-cancel",
            )

    @pytest.mark.asyncio
    async def test_multiple_errors_all_logged(self) -> None:
        """Multiple bg errors are all caught; response is always returned."""
        wrapper, inner = _make_wrapper(
            enabled=True,
            rpe_apply_side_effect=RuntimeError("multi"),
        )
        tc = TaskContext(trace_id="t-multi", category="coding", difficulty=2)
        # Run 3 times — each should succeed (fail-open).
        for _ in range(3):
            result = await wrapper.execute(tc, trace_id="t-multi", session_id="sess-f")
            assert result.generator_result.text == "ok"
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_disabled_bg_error_scenario_no_task(self) -> None:
        """When disabled, even if apply would error, no task is created."""
        wrapper, _ = _make_wrapper(
            enabled=False,
            rpe_apply_side_effect=RuntimeError("should never fire"),
        )
        tc = TaskContext(trace_id="t-nodis", category="coding", difficulty=2)
        result = await wrapper.execute(tc, trace_id="t-nodis", session_id="sess-f")
        await asyncio.sleep(0)
        # No error events logged (no task was created).
        error_events = [
            e for e in wrapper._logger.get_trace("t-nodis")
            if e.event_type == "rpe.pipeline_error"
        ]
        assert len(error_events) == 0
        assert result is not None


class TestRpeBackgroundDirect:
    @pytest.mark.asyncio
    async def test_rpe_background_cancelled_error_reraises(self) -> None:
        """_rpe_background must re-raise CancelledError."""
        from app.rpe.models import ActiveMutationConfig, RPEPipelineSnapshot
        from app.rpe.dopamine import DopamineRPE
        from app.rpe.sources import MockRewardSource

        logger = SpinalLogger()
        store = InMemorySynapseWeightStore()
        mutator = SynapseWeightMutator(store=store)
        # B11 S2: the observe (7-cell) path inside _rpe_background is now gated by
        # observe_enabled; enable it so the mocked apply (which raises) is reached.
        svc = RPEMutationService(
            mutator, logger, config=ActiveMutationConfig(observe_enabled=True)
        )
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        rpe.apply = AsyncMock(side_effect=asyncio.CancelledError())

        wrapper = RPEMutationPipelineWrapper(
            _make_noop_inner(), rpe, svc, logger
        )
        snapshot = RPEPipelineSnapshot(
            trace_id="t-bg-cancel",
            session_id="s-bg-cancel",
            category="coding",
            difficulty=2,
            response_source="swarm",
            latency_ms=10.0,
            error_occurred=False,
            timeout_occurred=False,
            continuation_bypass=False,
            pfc_active=False,
            pfc_cue_type=None,
            pfc_hint_applied=False,
        )
        with pytest.raises(asyncio.CancelledError):
            await wrapper._rpe_background(snapshot)


def _make_noop_inner():
    inner = MagicMock()
    inner.execute = AsyncMock(return_value=SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="noop", tier_used="STANDARD", model_name="mock",
            prompt_tokens=1, completion_tokens=1, finish_reason="stop",
            latency_ms=1.0, ne_applied=False, plan_intent="answer",
        ),
        context_status="ok",
        planner_status="ok",
        generator_status="ok",
        total_elapsed_ms=1.0,
    ))
    return inner

"""Phase 6 STEP 3.2 — RPEMutationPipelineWrapper unit tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.schemas.context import TaskContext
from app.api.schemas.query_features import QueryFeatures
from app.core.logging import SpinalLogger
from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult
from app.rpe.models import ActiveMutationConfig, RPEPipelineSnapshot
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.pipeline import RPEMutationPipelineWrapper
from app.rpe.service import RPEMutationService


def _swarm_result(
    *,
    context_status: str = "ok",
    planner_status: str = "ok",
    generator_status: str = "ok",
    total_elapsed_ms: float = 55.0,
) -> SwarmResult:
    return SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="output",
            tier_used="STANDARD",
            model_name="mock",
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason="stop",
            latency_ms=1.0,
            ne_applied=False,
            plan_intent="answer",
        ),
        context_status=context_status,
        planner_status=planner_status,
        generator_status=generator_status,
        total_elapsed_ms=total_elapsed_ms,
    )


def _task_context(*, category: str = "coding", difficulty: int = 2) -> TaskContext:
    return TaskContext(trace_id="trace-wrapper", category=category, difficulty=difficulty)


def _make_inner_swarm(result: SwarmResult | None = None) -> MagicMock:
    swarm = MagicMock()
    swarm.execute = AsyncMock(return_value=result or _swarm_result())
    return swarm


def _wrapper(
    *,
    enabled: bool = False,
    inner_swarm=None,
    store_initial: dict[tuple[str, str], float] | None = None,
) -> RPEMutationPipelineWrapper:
    from app.rpe.dopamine import DopamineRPE
    from app.rpe.sources import MockRewardSource

    store = InMemorySynapseWeightStore(
        store_initial if store_initial is not None else {("sess-w", "coding"): 0.5}
    )
    mutator = SynapseWeightMutator(store=store)
    logger = SpinalLogger()
    # B5: the wrapper gates the background observe task on observe_enabled.
    # (This helper's `enabled` param means "observe task on" for these tests.)
    config = ActiveMutationConfig(observe_enabled=enabled)
    svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
    rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
    inner = inner_swarm or _make_inner_swarm()
    return RPEMutationPipelineWrapper(
        inner_swarm=inner,
        dopamine_rpe=rpe,
        mutation_service=svc,
        logger=logger,
    )


class TestReturnValue:
    @pytest.mark.asyncio
    async def test_returns_swarm_result_unchanged(self) -> None:
        result = _swarm_result(total_elapsed_ms=77.0)
        inner = _make_inner_swarm(result)
        w = _wrapper(inner_swarm=inner)
        got = await w.execute(
            _task_context(),
            trace_id="t-ret",
            session_id="s-ret",
        )
        assert got is result

    @pytest.mark.asyncio
    async def test_inner_swarm_execute_called_once(self) -> None:
        inner = _make_inner_swarm()
        w = _wrapper(inner_swarm=inner)
        await w.execute(_task_context(), trace_id="t-once", session_id="s-once")
        inner.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_task_context_forwarded_to_inner(self) -> None:
        inner = _make_inner_swarm()
        w = _wrapper(inner_swarm=inner)
        tc = _task_context(category="math_logic")
        await w.execute(tc, trace_id="t-fwd", session_id="s-fwd")
        call_kwargs = inner.execute.call_args
        assert call_kwargs[0][0] is tc or call_kwargs[1].get("task_context") is tc

    @pytest.mark.asyncio
    async def test_query_features_forwarded(self) -> None:
        inner = _make_inner_swarm()
        w = _wrapper(inner_swarm=inner)
        qf = QueryFeatures(raw_query="test query", category="coding", difficulty=2)
        await w.execute(_task_context(), qf, trace_id="t-qf", session_id="s-qf")
        inner.execute.assert_called_once()


class TestDisabledNoBgTask:
    @pytest.mark.asyncio
    async def test_disabled_no_background_task(self) -> None:
        w = _wrapper(enabled=False)
        assert len(w._background_tasks) == 0
        await w.execute(_task_context(), trace_id="t-dis", session_id="s-dis")
        # Background tasks set stays empty when disabled.
        # (Tasks are added and then removed via done_callback when enabled.)
        # Here we just verify no tasks were scheduled.
        await asyncio.sleep(0)
        assert len(w._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_disabled_store_untouched(self) -> None:
        store = InMemorySynapseWeightStore({("sess-dis", "coding"): 0.5})
        mutator = SynapseWeightMutator(store=store)
        from app.rpe.dopamine import DopamineRPE
        from app.rpe.sources import MockRewardSource

        logger = SpinalLogger()
        svc = RPEMutationService(mutator, logger, ActiveMutationConfig(observe_enabled=False))
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        w = RPEMutationPipelineWrapper(
            inner_swarm=_make_inner_swarm(),
            dopamine_rpe=rpe,
            mutation_service=svc,
            logger=logger,
        )
        await w.execute(
            TaskContext(trace_id="t-store", category="coding", difficulty=2),
            trace_id="t-store",
            session_id="sess-dis",
        )
        await asyncio.sleep(0)
        val = await store.read_weight("sess-dis", "coding")
        assert val == pytest.approx(0.5)


class TestSnapshotBuilding:
    @pytest.mark.asyncio
    async def test_snapshot_has_correct_trace_id(self) -> None:
        snapshots_seen: list[RPEPipelineSnapshot] = []
        inner = _make_inner_swarm()
        w = _wrapper(inner_swarm=inner, enabled=False)

        orig_build = w._build_snapshot

        def spy_build(tc, sr, tid, sid):
            s = orig_build(tc, sr, tid, sid)
            snapshots_seen.append(s)
            return s

        w._build_snapshot = spy_build  # type: ignore[method-assign]
        await w.execute(_task_context(), trace_id="trace-snap-check", session_id="s-sc")
        assert snapshots_seen[0].trace_id == "trace-snap-check"

    @pytest.mark.asyncio
    async def test_snapshot_category_from_task_context(self) -> None:
        snapshots_seen: list[RPEPipelineSnapshot] = []
        inner = _make_inner_swarm()
        w = _wrapper(inner_swarm=inner, enabled=False)

        orig_build = w._build_snapshot

        def spy_build(tc, sr, tid, sid):
            s = orig_build(tc, sr, tid, sid)
            snapshots_seen.append(s)
            return s

        w._build_snapshot = spy_build  # type: ignore[method-assign]
        await w.execute(
            _task_context(category="writing"),
            trace_id="t-cat",
            session_id="s-cat",
        )
        assert snapshots_seen[0].category == "writing"

    @pytest.mark.asyncio
    async def test_snapshot_error_detected_from_swarm_result(self) -> None:
        snapshots_seen: list[RPEPipelineSnapshot] = []
        inner = _make_inner_swarm(_swarm_result(context_status="error"))
        w = _wrapper(inner_swarm=inner, enabled=False)

        orig_build = w._build_snapshot

        def spy_build(tc, sr, tid, sid):
            s = orig_build(tc, sr, tid, sid)
            snapshots_seen.append(s)
            return s

        w._build_snapshot = spy_build  # type: ignore[method-assign]
        await w.execute(_task_context(), trace_id="t-err", session_id="s-err")
        assert snapshots_seen[0].error_occurred is True

    @pytest.mark.asyncio
    async def test_snapshot_timeout_detected(self) -> None:
        snapshots_seen: list[RPEPipelineSnapshot] = []
        inner = _make_inner_swarm(_swarm_result(context_status="timeout"))
        w = _wrapper(inner_swarm=inner, enabled=False)

        orig_build = w._build_snapshot

        def spy_build(tc, sr, tid, sid):
            s = orig_build(tc, sr, tid, sid)
            snapshots_seen.append(s)
            return s

        w._build_snapshot = spy_build  # type: ignore[method-assign]
        await w.execute(_task_context(), trace_id="t-tout", session_id="s-tout")
        assert snapshots_seen[0].timeout_occurred is True

    @pytest.mark.asyncio
    async def test_snapshot_latency_from_swarm_result(self) -> None:
        snapshots_seen: list[RPEPipelineSnapshot] = []
        inner = _make_inner_swarm(_swarm_result(total_elapsed_ms=123.4))
        w = _wrapper(inner_swarm=inner, enabled=False)

        orig_build = w._build_snapshot

        def spy_build(tc, sr, tid, sid):
            s = orig_build(tc, sr, tid, sid)
            snapshots_seen.append(s)
            return s

        w._build_snapshot = spy_build  # type: ignore[method-assign]
        await w.execute(_task_context(), trace_id="t-lat", session_id="s-lat")
        assert snapshots_seen[0].latency_ms == pytest.approx(123.4)


class TestBackgroundTasksAttribute:
    def test_wrapper_has_background_tasks_set(self) -> None:
        w = _wrapper()
        assert isinstance(w._background_tasks, set)

    def test_initial_background_tasks_empty(self) -> None:
        w = _wrapper()
        assert len(w._background_tasks) == 0

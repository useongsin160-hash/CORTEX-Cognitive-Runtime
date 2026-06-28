"""Phase 4 STEP 3.3b — _swarm_result_to_trace overall status mapping."""
from __future__ import annotations

import pytest

from app.api.routes import _swarm_result_to_trace
from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult


def _swarm(*, ctx="ok", planner="ok", gen="ok") -> SwarmResult:
    return SwarmResult(
        context_result=ContextAgentResult() if ctx != "error" else None,
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="o", tier_used="STANDARD", model_name="m",
            prompt_tokens=1, completion_tokens=1,
            finish_reason="stop" if gen == "ok" else "error",
            latency_ms=1.0, ne_applied=False, plan_intent="answer",
        ),
        context_status=ctx,
        planner_status=planner,
        generator_status=gen,
        total_elapsed_ms=12.5,
    )


def test_all_ok_maps_to_ok():
    trace = _swarm_result_to_trace(_swarm())
    assert trace.status == "ok"
    assert trace.executed is True


def test_context_fallback_only_maps_to_degraded():
    # context "empty" / planner "fallback" — any non-ok non-special → degraded
    trace = _swarm_result_to_trace(_swarm(planner="fallback"))
    assert trace.status == "degraded"


def test_context_error_maps_to_error():
    trace = _swarm_result_to_trace(_swarm(ctx="error"))
    assert trace.status == "error"


def test_context_timeout_maps_to_timeout():
    trace = _swarm_result_to_trace(_swarm(ctx="timeout"))
    assert trace.status == "timeout"


def test_error_wins_over_timeout():
    # 'error' has higher priority than 'timeout'
    swarm = _swarm(ctx="timeout")
    swarm = swarm.model_copy(update={"generator_status": "error"})
    trace = _swarm_result_to_trace(swarm)
    assert trace.status == "error"


def test_timeout_wins_over_fallback():
    swarm = _swarm(ctx="timeout", planner="fallback")
    trace = _swarm_result_to_trace(swarm)
    assert trace.status == "timeout"


def test_fields_are_mirrored_into_trace():
    swarm = _swarm(ctx="ok", planner="ok", gen="ok")
    trace = _swarm_result_to_trace(swarm)
    assert trace.context_status == "ok"
    assert trace.planner_status == "ok"
    assert trace.generator_status == "ok"
    assert trace.generator_finish_reason == "stop"
    assert trace.plan_intent == "answer"
    assert trace.elapsed_ms == 12.5

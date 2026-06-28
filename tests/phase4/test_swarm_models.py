"""Phase 4 STEP 3.2 — SwarmResult model."""
from __future__ import annotations

import json

from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult


def _final_plan() -> FinalPlan:
    return FinalPlan(intent="answer", prompt_for_generator="[QUERY] q")


def _generator_result() -> GeneratorResult:
    return GeneratorResult(
        text="out", tier_used="STANDARD", model_name="m",
        prompt_tokens=1, completion_tokens=1, finish_reason="stop",
        latency_ms=1.0, ne_applied=False, plan_intent="answer",
    )


def test_swarm_result_json_serializable():
    result = SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=_final_plan(),
        generator_result=_generator_result(),
        context_status="ok",
        planner_status="ok",
        generator_status="ok",
        total_elapsed_ms=12.3,
    )
    dumped = json.loads(result.model_dump_json())
    assert dumped["context_status"] == "ok"
    assert dumped["final_plan"]["intent"] == "answer"


def test_swarm_result_allows_none_context():
    result = SwarmResult(
        context_result=None,
        final_plan=_final_plan(),
        generator_result=_generator_result(),
        context_status="error",
        planner_status="ok",
        generator_status="ok",
        total_elapsed_ms=5.0,
    )
    assert result.context_result is None


def test_swarm_result_timing_fields_default_none():
    result = SwarmResult(
        context_result=None,
        final_plan=_final_plan(),
        generator_result=_generator_result(),
        context_status="ok",
        planner_status="ok",
        generator_status="ok",
        total_elapsed_ms=1.0,
    )
    assert result.context_elapsed_ms is None
    assert result.inject_elapsed_ms is None

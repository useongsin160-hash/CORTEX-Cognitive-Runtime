"""Phase 4 STEP 3.3a — QueryResponse / SwarmTrace schema."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.api.schemas.response import QueryResponse, SwarmTrace


def test_swarm_trace_minimal_construction_and_json():
    trace = SwarmTrace(executed=True, status="ok")
    dumped = json.loads(trace.model_dump_json())
    assert dumped["executed"] is True
    assert dumped["status"] == "ok"
    assert dumped["elapsed_ms"] is None


def test_swarm_trace_full_fields():
    trace = SwarmTrace(
        executed=True, status="degraded", elapsed_ms=120.5,
        context_status="empty", planner_status="ok", generator_status="ok",
        generator_finish_reason="stop", plan_intent="answer",
    )
    assert trace.context_status == "empty"
    assert trace.plan_intent == "answer"


@pytest.mark.parametrize("bad", ["maybe", "OK", "stopped"])
def test_swarm_trace_status_rejects_unknown_value(bad):
    with pytest.raises(ValidationError):
        SwarmTrace(executed=True, status=bad)


def test_query_response_defaults_new_fields_to_none():
    qr = QueryResponse(trace_id="t", answer="a", path_taken="thalamus")
    assert qr.response_source is None
    assert qr.swarm_trace is None


@pytest.mark.parametrize("source", [
    "thalamus", "exact_cache", "semantic_cache", "tier_1_5", "swarm", "fallback",
])
def test_query_response_response_source_enum(source):
    qr = QueryResponse(trace_id="t", answer="a", path_taken="x",
                       response_source=source)
    assert qr.response_source == source


@pytest.mark.parametrize("bad", ["thalamic", "ROUTED", "cache", ""])
def test_query_response_rejects_unknown_response_source(bad):
    with pytest.raises(ValidationError):
        QueryResponse(trace_id="t", answer="a", path_taken="x",
                      response_source=bad)


def test_query_response_round_trips_through_json():
    inner = SwarmTrace(executed=True, status="ok", elapsed_ms=10.0,
                       context_status="ok", planner_status="ok",
                       generator_status="ok", generator_finish_reason="stop",
                       plan_intent="answer")
    qr = QueryResponse(
        trace_id="t", answer="hello", path_taken="routed_standard",
        response_source="swarm", swarm_trace=inner,
        selected_tier="STANDARD",
    )
    payload = json.loads(qr.model_dump_json())
    qr2 = QueryResponse.model_validate(payload)
    assert qr2.swarm_trace is not None
    assert qr2.swarm_trace.executed is True
    assert qr2.response_source == "swarm"

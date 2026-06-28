"""Phase 4 STEP 3.1 — Planner/Generator data models."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.execution.plan_models import FinalPlan, GeneratorResult, PrePlan


def test_pre_plan_construction_and_json():
    p = PrePlan(intent="answer", steps_outline=["a", "b"],
               requires_context=False, confidence=0.6)
    dumped = json.loads(p.model_dump_json())
    assert dumped["intent"] == "answer"
    assert dumped["steps_outline"] == ["a", "b"]


def test_final_plan_construction_and_json():
    fp = FinalPlan(
        intent="code_generation",
        steps=["s1", "s2"],
        context_used=True,
        context_chunk_ids=["c1"],
        prompt_for_generator="[QUERY] x",
        pre_plan_modified=True,
    )
    dumped = json.loads(fp.model_dump_json())
    assert dumped["context_used"] is True
    assert dumped["context_chunk_ids"] == ["c1"]


def test_generator_result_construction_and_json():
    gr = GeneratorResult(
        text="hello", tier_used="STANDARD", model_name="m",
        prompt_tokens=10, completion_tokens=5, finish_reason="stop",
        latency_ms=42.0, ne_applied=False, plan_intent="answer",
    )
    dumped = json.loads(gr.model_dump_json())
    assert dumped["tier_used"] == "STANDARD"
    assert dumped["fallback_candidate"] is None


@pytest.mark.parametrize("conf", [-0.1, 1.1])
def test_pre_plan_confidence_out_of_range_rejected(conf):
    with pytest.raises(ValidationError):
        PrePlan(intent="answer", confidence=conf)


def test_pre_plan_confidence_boundary_accepted():
    assert PrePlan(intent="answer", confidence=0.0).confidence == 0.0
    assert PrePlan(intent="answer", confidence=1.0).confidence == 1.0

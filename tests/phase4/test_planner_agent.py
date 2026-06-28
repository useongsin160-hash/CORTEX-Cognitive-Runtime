"""Phase 4 STEP 3.1 / STEP 5.2.5 — PlannerAgent (Micro-Sync 2단계)."""
from __future__ import annotations

import pytest

from app.execution.context_models import ContextAgentResult, RetrievedContext
from app.execution.planner_agent import PlannerAgent


@pytest.fixture
def planner() -> PlannerAgent:
    return PlannerAgent()


# ── create_pre_plan — intent 분류 ────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent", [
    ("Write a Python function to sort a list", "code_generation"),
    ("Compare X and Y and evaluate them", "analysis"),
    ("Tell me a story about a dragon", "creative"),
    ("What is the capital of France", "answer"),
    ("안녕하세요", "general"),
])
async def test_intent_classification(planner, query, expected_intent):
    pre = await planner.create_pre_plan(query)
    assert pre.intent == expected_intent


@pytest.mark.asyncio
@pytest.mark.parametrize("difficulty", [4, 5])
async def test_high_difficulty_lowers_confidence(planner, difficulty):
    # B12: confidence drops on the high band (>=4), not at the middle (3).
    pre = await planner.create_pre_plan("solve this", difficulty=difficulty)
    assert pre.confidence == 0.4


@pytest.mark.asyncio
@pytest.mark.parametrize("difficulty", [1, 2, 3])
async def test_low_difficulty_confidence(planner, difficulty):
    pre = await planner.create_pre_plan("hello", difficulty=difficulty)
    assert pre.confidence == 0.6


@pytest.mark.asyncio
async def test_high_difficulty_prepends_deep_analysis(planner):
    # >=4 prepends the deep-analysis step; 3 (now the middle) does not.
    pre = await planner.create_pre_plan("write code", difficulty=4)
    assert pre.steps_outline[0] == "Deep analysis"
    pre3 = await planner.create_pre_plan("write code", difficulty=3)
    assert pre3.steps_outline[0] != "Deep analysis"


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected", [
    ("write a function", True),       # code_generation
    ("analyze this data", True),      # analysis
    ("tell me a story", False),       # creative
    ("안녕", False),                  # general
])
async def test_requires_context(planner, query, expected):
    pre = await planner.create_pre_plan(query)
    assert pre.requires_context is expected


# ── inject_context ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inject_context_none_means_unused(planner):
    pre = await planner.create_pre_plan("write a function")
    final = await planner.inject_context(pre, None, "write a function")
    assert final.context_used is False
    assert "[CONTEXT]" not in final.prompt_for_generator
    assert "[QUERY]" in final.prompt_for_generator
    assert "[INTENT]" in final.prompt_for_generator


@pytest.mark.asyncio
async def test_inject_context_all_masked_means_unused(planner):
    pre = await planner.create_pre_plan("write a function")
    ctx = ContextAgentResult(
        retrieved=[RetrievedContext(chunk_id="c1", text="t", similarity=0.2,
                                    masked_by_gaba=True)],
    )
    final = await planner.inject_context(pre, ctx, "write a function")
    assert final.context_used is False


@pytest.mark.asyncio
async def test_inject_context_with_unmasked_chunk(planner):
    pre = await planner.create_pre_plan("write a function")
    ctx = ContextAgentResult(
        retrieved=[
            RetrievedContext(chunk_id="c1", text="useful", similarity=0.9),
            RetrievedContext(chunk_id="c2", text="noise", similarity=0.2,
                             masked_by_gaba=True),
        ],
    )
    final = await planner.inject_context(pre, ctx, "write a function")
    assert final.context_used is True
    assert final.context_chunk_ids == ["c1"]
    assert "[CONTEXT]" in final.prompt_for_generator
    assert "useful" in final.prompt_for_generator
    assert "noise" not in final.prompt_for_generator


@pytest.mark.asyncio
async def test_pre_plan_modified_when_context_used_and_required(planner):
    pre = await planner.create_pre_plan("write a function")  # requires_context
    ctx = ContextAgentResult(
        retrieved=[RetrievedContext(chunk_id="c1", text="t", similarity=0.9)],
    )
    final = await planner.inject_context(pre, ctx, "write a function")
    assert final.pre_plan_modified is True
    assert final.steps[0] == "Review retrieved context"


@pytest.mark.asyncio
async def test_pre_plan_not_modified_when_context_not_required(planner):
    pre = await planner.create_pre_plan("tell me a story")  # creative, no context
    ctx = ContextAgentResult(
        retrieved=[RetrievedContext(chunk_id="c1", text="t", similarity=0.9)],
    )
    final = await planner.inject_context(pre, ctx, "tell me a story")
    assert final.pre_plan_modified is False


# ── STEP 5.2.5: category fallback ───────────────────────────────────────

@pytest.mark.asyncio
async def test_regex_wins_over_category_when_explicit():
    """regex가 명확히 잡히면 category fallback으로 덮어쓰지 않는다."""
    planner = PlannerAgent()
    pre = await planner.create_pre_plan("Write Python code", category="writing")
    assert pre.intent == "code_generation"  # regex wins, not creative


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category,expected_intent", [
    ("이거 좀 봐줘", "coding", "code_generation"),
    ("이 결과를 봐줘", "data_analysis", "analysis"),
    ("이 구조를 봐줘", "system_design", "analysis"),
    ("이 수식을 봐줘", "math_logic", "analysis"),
    ("이 문장을 봐줘", "writing", "creative"),
    ("이 게임 구조를 봐줘", "game_design", "creative"),
    ("그냥 물어보는 거야", "general", "general"),
])
async def test_category_fallback_when_regex_general(query, category, expected_intent):
    """regex가 general이면 category가 intent를 결정한다."""
    planner = PlannerAgent()
    pre = await planner.create_pre_plan(query, category=category)
    assert pre.intent == expected_intent


@pytest.mark.asyncio
async def test_unknown_category_falls_back_to_general():
    planner = PlannerAgent()
    pre = await planner.create_pre_plan("이거 봐줘", category="unknown")
    assert pre.intent == "general"


@pytest.mark.asyncio
async def test_no_category_still_returns_general_when_regex_general():
    planner = PlannerAgent()
    pre = await planner.create_pre_plan("이거 봐줘", category=None)
    assert pre.intent == "general"


@pytest.mark.asyncio
async def test_category_none_does_not_override_regex():
    """category=None이어도 regex가 잡힌 경우 regex 결과를 반환한다."""
    planner = PlannerAgent()
    pre = await planner.create_pre_plan("Write Python code", category=None)
    assert pre.intent == "code_generation"

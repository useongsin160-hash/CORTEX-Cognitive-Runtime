import asyncio

import pytest

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import get_spinal_logger
from app.core.model_tier import ModelTier
from app.routing import pfc_stub
from app.routing.lc import LocusCoeruleus
from app.routing.semantic_evaluator import (
    CATEGORIES,
    EvaluationResult,
    SemanticEvaluator,
)
from app.routing.skip_router import SkipLogicRouter
from app.routing.tier1_5 import Tier15Augmentation


# ---- SemanticEvaluator ---------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_short_greeting_is_difficulty_1():
    ev = SemanticEvaluator()
    result = await ev.evaluate("hi")
    assert result.difficulty == 1


@pytest.mark.asyncio
async def test_evaluator_complex_design_request_is_very_hard():
    # B12 5-stage: hard keywords (design/prove/trade-off/optimization) →
    # VERY_HARD(4); only a *long* (>60 words) hard prompt escalates to 5.
    ev = SemanticEvaluator()
    prompt = (
        "Design a scalable architecture for a multi-region payment system "
        "and prove its consistency guarantees under network partition. "
        "Include a trade-off analysis for synchronous versus asynchronous "
        "replication and discuss the optimization opportunities at each layer."
    )
    result = await ev.evaluate(prompt)
    assert result.difficulty == 4


@pytest.mark.asyncio
async def test_evaluator_long_hard_request_is_deep_thinking():
    # Hard keyword + >60 words → DEEP_THINKING(5).
    ev = SemanticEvaluator()
    prompt = (
        "Design and prove a scalable architecture for a multi-region payment "
        "system with strong consistency under network partition, then optimize "
        "the throughput while preserving the trade-off analysis across "
        "synchronous and asynchronous replication paths, and walk through every "
        "failure mode in detail with an explanation of how each layer recovers "
        "and why the chosen optimization holds under sustained heavy load "
        "across many concurrent regional clusters and shifting traffic."
    )
    result = await ev.evaluate(prompt)
    assert result.difficulty == 5


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("I have a python bug in my class function", "coding"),
        ("design a quest for the boss level NPC", "game_design"),
        ("prove the theorem about this equation algorithm", "math_logic"),
        ("write a short essay summary translate it", "writing"),
        ("analyze the csv data with regression statistics", "data_analysis"),
        ("design the architecture for our infrastructure scalability", "system_design"),
        ("tell me a joke about a friendly platypus", "general"),
    ],
)
@pytest.mark.asyncio
async def test_evaluator_classifies_each_category(prompt, expected):
    ev = SemanticEvaluator()
    result = await ev.evaluate(prompt)
    assert result.category == expected
    assert result.category in CATEGORIES


@pytest.mark.asyncio
async def test_evaluator_is_stateless_no_side_effects():
    """SemanticEvaluator is a sensor: repeated calls with the same input
    return identical results, and no per-call state accumulates on the
    instance. The Phase 3 STEP 2 refactor introduced a single
    configuration attribute (_centroid_store) for dependency injection;
    that holds a fixed reference, not accumulated state, so the
    snapshot of __dict__ must remain identical across calls."""
    ev = SemanticEvaluator()
    before = dict(vars(ev))
    a = await ev.evaluate("How do I parse a CSV in python?")
    b = await ev.evaluate("How do I parse a CSV in python?")
    after = dict(vars(ev))
    assert a == b
    assert before == after


# ---- LocusCoeruleus ------------------------------------------------------

@pytest.mark.asyncio
async def test_lc_creates_task_context_with_registered_trace():
    lc = LocusCoeruleus()
    logger = get_spinal_logger()
    ev_result = EvaluationResult(
        difficulty=2, category="coding", embedding=[], confidence=0.5
    )
    ctx = await lc.process("how do I sort a list", ev_result)
    assert isinstance(ctx, TaskContext)
    assert ctx.trace_id
    # Trace must be registered with at least the lc.dispatched event.
    events = logger.get_trace(ctx.trace_id)
    assert any(e.event_type == "lc.dispatched" for e in events)
    await asyncio.sleep(0)  # drain create_task'd PFC stub


@pytest.mark.asyncio
async def test_lc_difficulty_4_sets_ne_boost_true():
    # B12 5-stage: NE boost fires on the high band (VERY_HARD/DEEP_THINKING).
    lc = LocusCoeruleus()
    ev_result = EvaluationResult(
        difficulty=4, category="system_design", embedding=[], confidence=0.8
    )
    ctx = await lc.process("design a complex system", ev_result)
    assert ctx.difficulty == Difficulty.VERY_HARD
    assert ctx.ne_boost is True
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lc_difficulty_3_keeps_ne_boost_false():
    # 3 is now the MIDDLE rung — no NE boost (threshold is >=4).
    lc = LocusCoeruleus()
    ev_result = EvaluationResult(
        difficulty=3, category="system_design", embedding=[], confidence=0.8
    )
    ctx = await lc.process("design a system", ev_result)
    assert ctx.difficulty == Difficulty.HARD
    assert ctx.ne_boost is False
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lc_difficulty_1_keeps_ne_boost_false():
    lc = LocusCoeruleus()
    ev_result = EvaluationResult(
        difficulty=1, category="general", embedding=[], confidence=0.3
    )
    ctx = await lc.process("hi", ev_result)
    assert ctx.ne_boost is False
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "difficulty, expected_tier",
    [
        (1, ModelTier.LIGHTWEIGHT),
        (2, ModelTier.MEDIUM),
        (3, ModelTier.STANDARD),   # demo: "난이도 3 → STANDARD 칸" 재현
        (4, ModelTier.HEAVY),
        (5, ModelTier.DEEP_THINKING),
    ],
)
async def test_lc_difficulty_selects_tier_one_to_one(difficulty, expected_tier):
    """B12: difficulty alone selects the tier (1:1 with ModelTier), independent
    of category. No Epinephrine injected → tier still comes from difficulty."""
    lc = LocusCoeruleus()
    ev_result = EvaluationResult(
        difficulty=difficulty, category="general", embedding=[], confidence=0.5
    )
    ctx = await lc.process("p", ev_result)
    assert ctx.selected_tier == expected_tier
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lc_dispatches_pfc_via_create_task(monkeypatch):
    """LC must not block on PFC — patched stub uses an Event to verify
    LC.process returns BEFORE the stub finishes its work."""
    started = asyncio.Event()
    finished = asyncio.Event()

    async def fake_notify(trace_id, evaluator_result):
        started.set()
        await asyncio.sleep(0.05)  # simulate PFC latency
        finished.set()

    monkeypatch.setattr("app.routing.lc.notify_pfc", fake_notify)

    lc = LocusCoeruleus()
    ev_result = EvaluationResult(
        difficulty=2, category="coding", embedding=[], confidence=0.5
    )
    ctx = await lc.process("dispatch test", ev_result)

    # Right after process(), the stub must NOT have completed yet — proves
    # LC dispatched it as a background task rather than awaiting it.
    assert finished.is_set() is False
    assert ctx.trace_id

    # And it does eventually run.
    await finished.wait()
    assert started.is_set() and finished.is_set()


# ---- Tier-1.5 Augmentation ----------------------------------------------

class _StubClient:
    """should_activate must never call the LLM client — this stub fails loudly
    if it does. execute() is covered in test_tier1_5_execute.py."""

    async def generate(self, *args, **kwargs):  # pragma: no cover - unused here
        raise AssertionError("should_activate must not call the LLM client")


@pytest.fixture
def tier15():
    return Tier15Augmentation(llm_client=_StubClient())


def _ctx(diff: Difficulty) -> TaskContext:
    return TaskContext(trace_id="t", difficulty=diff)


@pytest.mark.asyncio
async def test_tier15_easy_in_band_activates(tier15):
    assert await tier15.should_activate(_ctx(Difficulty.EASY), 0.85) is True


@pytest.mark.asyncio
async def test_tier15_medium_in_band_does_not_activate(tier15):
    assert await tier15.should_activate(_ctx(Difficulty.MEDIUM), 0.85) is False


@pytest.mark.asyncio
async def test_tier15_easy_above_band_does_not_activate(tier15):
    # 0.95 belongs to Tier-2 SemanticCache, not Tier-1.5.
    assert await tier15.should_activate(_ctx(Difficulty.EASY), 0.95) is False


@pytest.mark.asyncio
async def test_tier15_easy_no_similarity_does_not_activate(tier15):
    assert await tier15.should_activate(_ctx(Difficulty.EASY), None) is False


# ---- SkipLogicRouter -----------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "difficulty, expected_path",
    [
        (Difficulty.EASY, "lightweight"),
        (Difficulty.MEDIUM, "standard"),
        (Difficulty.HARD, "standard"),          # B12: 2·3 grouped → standard
        (Difficulty.VERY_HARD, "full_pipeline"),  # 4·5 grouped → full_pipeline
        (Difficulty.DEEP_THINKING, "full_pipeline"),
    ],
)
async def test_skip_router_maps_difficulty_to_path(difficulty, expected_path):
    router = SkipLogicRouter()
    decision = await router.route(_ctx(difficulty))
    assert decision.path == expected_path
    assert decision.reason


# Sanity: pfc_stub itself logs its event when called directly.
@pytest.mark.asyncio
async def test_pfc_stub_logs_event():
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    await pfc_stub.notify_pfc(
        trace_id,
        EvaluationResult(difficulty=2, category="coding", embedding=[], confidence=0.5),
    )
    events = logger.get_trace(trace_id)
    assert any(e.event_type == "pfc_stub_called" for e in events)

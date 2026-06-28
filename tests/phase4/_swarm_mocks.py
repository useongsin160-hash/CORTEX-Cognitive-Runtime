"""Shared mock agents for Phase 4 STEP 3.2 swarm tests."""
from __future__ import annotations

import asyncio
import time

from app.execution.context_models import ContextAgentResult, RetrievedContext
from app.execution.plan_models import FinalPlan, PrePlan


def context_result_with(unmasked: int = 1, masked: int = 0) -> ContextAgentResult:
    retrieved = (
        [RetrievedContext(chunk_id=f"u{i}", text=f"useful {i}", similarity=0.9)
         for i in range(unmasked)]
        + [RetrievedContext(chunk_id=f"m{i}", text=f"noise {i}", similarity=0.1,
                            masked_by_gaba=True)
           for i in range(masked)]
    )
    return ContextAgentResult(
        selected_categories=["coding"],
        retrieved=retrieved,
        filtered_count=masked,
    )


class MockContextAgent:
    """Configurable ContextAgent stand-in.

    delay: sleep before returning. raises: exception instance to raise.
    """

    def __init__(self, *, result=None, delay: float = 0.0, raises=None) -> None:
        self._result = result if result is not None else context_result_with()
        self._delay = delay
        self._raises = raises
        self.started_at: float | None = None

    async def retrieve(self, task_context, query_features=None):
        self.started_at = time.perf_counter()
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return self._result


class MockPlannerAgent:
    """ContextAgent와 동시 시작 추적용 + 예외 주입 가능한 Planner.

    create_pre_plan에 예외를 주입하면 swarm은 fallback pre_plan을 쓴다.
    inject_context는 항상 정상 동작 (FinalPlan 생성).
    """

    def __init__(self, *, pre_plan_raises=None) -> None:
        self._pre_plan_raises = pre_plan_raises
        self.create_started_at: float | None = None
        self.inject_called = False

    async def create_pre_plan(self, query: str, difficulty: int = 1, category: str | None = None) -> PrePlan:
        self.create_started_at = time.perf_counter()
        if self._pre_plan_raises is not None:
            raise self._pre_plan_raises
        return PrePlan(
            intent="answer",
            steps_outline=["Process query", "Generate response"],
            requires_context=True,
            confidence=0.6,
        )

    async def inject_context(self, pre_plan, context_result, query: str) -> FinalPlan:
        self.inject_called = True
        context_used = (
            context_result is not None
            and len(context_result.retrieved) > 0
            and any(not c.masked_by_gaba for c in context_result.retrieved)
        )
        chunk_ids = (
            [c.chunk_id for c in context_result.retrieved if not c.masked_by_gaba]
            if context_used else []
        )
        return FinalPlan(
            intent=pre_plan.intent,
            steps=list(pre_plan.steps_outline),
            context_used=context_used,
            context_chunk_ids=chunk_ids,
            prompt_for_generator=f"[QUERY] {query}",
            pre_plan_modified=context_used,
        )


class MockGeneratorAgent:
    """final_plan 인자를 기록하는 Generator stand-in."""

    def __init__(self) -> None:
        self.received_final_plan = None
        self.called_at: float | None = None

    async def generate(self, final_plan, task_context, base_params=None):
        from app.execution.plan_models import GeneratorResult

        self.called_at = time.perf_counter()
        self.received_final_plan = final_plan
        if final_plan is None:
            raise RuntimeError("GeneratorAgent requires final_plan")
        return GeneratorResult(
            text=f"[MOCK] {final_plan.prompt_for_generator}",
            tier_used="STANDARD",
            model_name="mock-model",
            prompt_tokens=5,
            completion_tokens=5,
            finish_reason="stop",
            latency_ms=1.0,
            ne_applied=False,
            plan_intent=final_plan.intent,
        )

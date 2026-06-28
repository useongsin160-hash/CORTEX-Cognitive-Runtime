"""Generator Agent — LLM 호출 + Norepinephrine 적용."""
from __future__ import annotations

from app.api.schemas.context import TaskContext
from app.core.model_tier import ModelTier
from app.execution.llm_client import LLMClientProtocol
from app.execution.params import GenerationParams
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.routing.neuromodulators import Norepinephrine


class GeneratorAgent:
    """LLM 호출 + Norepinephrine 파라미터 변조.

    원칙:
      - final_plan 필수 (pre_plan만으로 호출 금지 → RuntimeError)
      - Norepinephrine 발동 검사 후 GenerationParams 변조
      - LLMClientProtocol을 통해 호출 (mock/live 분리)

    STEP 3.1: 단위 호출만. STEP 3.2 이후 swarm에서 호출.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        norepinephrine: Norepinephrine,
    ) -> None:
        self._llm_client = llm_client
        self._norepinephrine = norepinephrine

    async def generate(
        self,
        final_plan: FinalPlan,
        task_context: TaskContext,
        base_params: GenerationParams | None = None,
    ) -> GeneratorResult:
        """final_plan 기반 생성.

        Raises:
            RuntimeError: final_plan이 None인 경우 (pre_plan-only 금지).
        """
        if final_plan is None:
            raise RuntimeError(
                "GeneratorAgent.generate() requires final_plan. "
                "pre_plan-only execution is forbidden."
            )

        tier = task_context.selected_tier or ModelTier.STANDARD
        if base_params is None:
            base_params = GenerationParams()

        modified_params = await self._norepinephrine.modify_params(
            params=base_params,
            tier=tier,
            ne_active=task_context.ne_boost,
        )

        try:
            llm_result = await self._llm_client.generate(
                prompt=final_plan.prompt_for_generator,
                tier=tier,
                params=modified_params,
            )
            return GeneratorResult(
                text=llm_result.text,
                tier_used=llm_result.tier_used,
                model_name=llm_result.model_name,
                prompt_tokens=llm_result.prompt_tokens,
                completion_tokens=llm_result.completion_tokens,
                finish_reason=llm_result.finish_reason,
                latency_ms=llm_result.latency_ms,
                ne_applied=modified_params.ne_applied,
                ne_reason=modified_params.ne_reason,
                plan_intent=final_plan.intent,
                fallback_candidate=None,
            )
        except Exception as exc:
            # Graceful Fallback 자리 (설계서 line 309-312).
            # CP3 실제 검증은 후속 STEP — 여기서는 안전 모드 결과만 반환.
            return GeneratorResult(
                text=f"[FALLBACK] Generator failed: {type(exc).__name__}",
                tier_used=tier.name,
                model_name="fallback",
                prompt_tokens=0,
                completion_tokens=0,
                finish_reason="error",
                latency_ms=0.0,
                ne_applied=False,
                ne_reason="generator_error",
                plan_intent=final_plan.intent,
                fallback_candidate=f"Error: {str(exc)[:200]}",
            )

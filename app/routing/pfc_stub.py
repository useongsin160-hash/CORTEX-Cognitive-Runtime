"""PFC Stub Module (DEPRECATED).

Phase 5 이전 LC notify_pfc 호환성을 위해 유지된다.
Phase 5 STEP 3에서 app.routing.pfc.PrefrontalCortex 본체가 도입되었고,
LC는 Phase 6에서 본 stub 호출 경로를 제거할 예정이다.

본 모듈은 Phase 6 진입 시점에 LC 리팩토링과 함께 제거된다.
사용처 (현재): app/routing/lc.py — Phase 2 호환을 위한 fire-and-forget notify.

신규 코드는 app.routing.pfc.PrefrontalCortex를 직접 사용한다.
"""
from __future__ import annotations

from app.core.logging import get_spinal_logger
from app.routing.semantic_evaluator import EvaluationResult

__deprecated__ = True
__deprecation_message__ = (
    "app.routing.pfc_stub is deprecated. "
    "Use app.routing.pfc.PrefrontalCortex directly. "
    "This module will be removed in Phase 6."
)


async def notify_pfc(trace_id: str, evaluator_result: EvaluationResult) -> None:
    logger = get_spinal_logger()
    await logger.log_event(
        trace_id=trace_id,
        module_name="routing.pfc_stub",
        event_type="pfc_stub_called",
        payload={
            "difficulty": evaluator_result.difficulty,
            "category": evaluator_result.category,
            "confidence": evaluator_result.confidence,
        },
    )

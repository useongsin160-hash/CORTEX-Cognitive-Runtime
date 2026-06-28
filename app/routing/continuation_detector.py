"""Phase 5 STEP 5 — ContinuationDetector: early-exit bypass 결정.

ContinuationDetector는 Sanitizer/Glycine 통과 직후, Thalamus 진입 직전에
호출된다. continuation cue + active_goal이 모두 충족되면 routes.py가
Thalamus/Cache/Tier-1.5를 우회하고 AsyncSwarm으로 직접 분기한다.

핵심 불변식:
- store read-only (touch/update/set_active/add_goal 호출 금지)
- LLM / embedder 호출 금지
- Phase 6 모듈 import 금지
- session_id 없으면 bypass 금지 (fail-open)
- active_goal 없으면 bypass 금지 (fail-open)
- store 예외 / detector 내부 예외는 fail-open (normal path 진행)
- asyncio.CancelledError는 절대 삼키지 않고 re-raise
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.api.schemas.context import ContinuationContext
from app.routing.cue_classifier import CueClassifier, CueDetection

if TYPE_CHECKING:
    from app.memory.session_goal_context import SessionGoalContext
    from app.memory.store import SessionGoalStore


ContinuationReason = Literal[
    "bypass",
    "no_session_id",
    "no_active_goal",
    "no_continuation_cue",
    "detector_error",
]


@dataclass(frozen=True)
class ContinuationDecision:
    """Detector 출력 — should_bypass + reason + 순수 snapshot."""

    should_bypass: bool
    cue_detection: CueDetection
    active_goal_snapshot: ContinuationContext | None
    reason: ContinuationReason


class ContinuationDetector:
    """Continuation cue + active_goal 결합 시 early-exit bypass 결정.

    routes.py는 Sanitizer/Glycine 호출 후 이 detector를 호출하고
    should_bypass=True이면 Thalamus/Cache/Tier-1.5 우회 + AsyncSwarm 직접 호출.
    """

    def __init__(
        self,
        cue_classifier: CueClassifier,
        session_goal_store: "SessionGoalStore",
        logger,
    ) -> None:
        self._cue_classifier = cue_classifier
        self._store = session_goal_store
        self._logger = logger

    async def detect(
        self,
        query: str,
        session_id: str | None,
        trace_id: str,
    ) -> ContinuationDecision:
        """Continuation bypass 후보 결정.

        실패 시 fail-open: should_bypass=False + reason="detector_error".
        CancelledError는 re-raise.
        """
        try:
            return await self._detect_inner(query, session_id, trace_id)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._safe_log(
                trace_id=trace_id,
                event_type="continuation.detector_error",
                payload={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return ContinuationDecision(
                should_bypass=False,
                cue_detection=CueDetection(
                    cue_type="none",
                    language="en",
                    matched_keyword=None,
                    confidence=0.0,
                ),
                active_goal_snapshot=None,
                reason="detector_error",
            )

    async def _detect_inner(
        self,
        query: str,
        session_id: str | None,
        trace_id: str,
    ) -> ContinuationDecision:
        # 1. session_id 없으면 fail-open
        if not session_id:
            cue_no_session = self._cue_classifier.classify(query)
            await self._safe_log(
                trace_id=trace_id,
                event_type="continuation.no_session_id",
                payload={"cue_type": cue_no_session.cue_type},
            )
            return ContinuationDecision(
                should_bypass=False,
                cue_detection=cue_no_session,
                active_goal_snapshot=None,
                reason="no_session_id",
            )

        # 2. cue 분류
        cue = self._cue_classifier.classify(query)
        if cue.cue_type != "continuation":
            return ContinuationDecision(
                should_bypass=False,
                cue_detection=cue,
                active_goal_snapshot=None,
                reason="no_continuation_cue",
            )

        # 3. session store에서 SessionGoalContext 조회 (read-only)
        context = await self._store.get_or_create_session(session_id)

        # 4. active_goal 확인
        active_goal = context.get_active_goal()
        if active_goal is None:
            await self._safe_log(
                trace_id=trace_id,
                event_type="continuation.no_active_goal",
                payload={
                    "cue_keyword": cue.matched_keyword,
                    "cue_language": cue.language,
                },
            )
            return ContinuationDecision(
                should_bypass=False,
                cue_detection=cue,
                active_goal_snapshot=None,
                reason="no_active_goal",
            )

        # 5. active_goal snapshot 생성 (순수 Pydantic, 원본 객체 미노출)
        snapshot = ContinuationContext(
            detected=True,
            cue_keyword=cue.matched_keyword,
            cue_language=cue.language,
            active_goal_id=active_goal.goal_id,
            active_goal_title=active_goal.title,
            active_goal_category=active_goal.category,
            active_goal_summary=active_goal.summary,
        )

        await self._safe_log(
            trace_id=trace_id,
            event_type="continuation.bypass_early_exit",
            payload={
                "cue_keyword": cue.matched_keyword,
                "cue_language": cue.language,
                "active_goal_id": active_goal.goal_id,
                "active_goal_category": active_goal.category,
            },
        )
        return ContinuationDecision(
            should_bypass=True,
            cue_detection=cue,
            active_goal_snapshot=snapshot,
            reason="bypass",
        )

    async def _safe_log(
        self,
        *,
        trace_id: str,
        event_type: str,
        payload: dict,
    ) -> None:
        """Logger 실패는 detector 동작을 막지 않는다 (fail-open)."""
        try:
            await self._logger.log_event(
                trace_id=trace_id,
                module_name="routing.continuation_detector",
                event_type=event_type,
                payload=payload,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            return

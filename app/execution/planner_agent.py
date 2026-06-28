"""Planner Agent — Micro-Sync 2단계 (pre_plan → inject_context)."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, PrePlan

if TYPE_CHECKING:
    from app.routing.pfc import PFCDecision, PFCIntegrationConfig


class PlannerAgent:
    """실행 계획 수립.

    Micro-Sync 2단계 분리 (설계서 line 273-276):
      1. create_pre_plan(): Context 없이 임시 뼈대 생성
      2. inject_context(): Context 완료 후 final_plan 확정

    Phase 5 STEP 4: optional pfc_decision 인자 도입.
      - cue type 매트릭스로 PFC/regex 우선순위 분기
      - pfc_decision=None일 때 Phase 4 STEP 5.2.5 패턴과 100% 동일 동작
    """

    # 휴리스틱 intent 분류. PHASE 6: PFC 도입 시 더 정교한 의도 추출.
    _INTENT_PATTERNS: dict[str, list[str]] = {
        "code_generation": [r"\bcode\b", r"\bfunction\b", r"\bclass\b",
                            r"\bimplement\b", r"구현", r"코드", r"디버그",
                            r"오류", r"함수", r"리팩토링"],
        "analysis": [r"\banalyze\b", r"\bcompare\b", r"\bevaluate\b",
                     r"분석", r"비교", r"평가", r"복잡도", r"통계", r"데이터"],
        "creative": [r"\bstory\b", r"\bwrite\b", r"\bcreate\b",
                     r"소설", r"창작", r"작성", r"글", r"스토리", r"기획",
                     r"캐릭터", r"세계관"],
        "answer": [r"\bwhat\b", r"\bwhy\b", r"\bhow\b",
                   r"뭐", r"왜", r"어떻게", r"설명"],
    }

    # Evaluator category → intent fallback (regex가 general일 때만 사용).
    _CATEGORY_TO_INTENT: dict[str, str] = {
        "coding": "code_generation",
        "math_logic": "analysis",
        "data_analysis": "analysis",
        "system_design": "analysis",
        "writing": "creative",
        "game_design": "creative",
        "general": "general",
    }

    _OUTLINE_TEMPLATES: dict[str, list[str]] = {
        "code_generation": ["Analyze requirements", "Design structure", "Generate code"],
        "analysis": ["Identify subjects", "Compare attributes", "Summarize findings"],
        "creative": ["Establish theme", "Develop narrative", "Refine output"],
        "answer": ["Locate facts", "Synthesize response"],
        "general": ["Process query", "Generate response"],
    }

    # PFC cue → outline prefix (planner_hint를 outline에 반영)
    _CUE_OUTLINE_PREFIX: dict[str, str] = {
        "completion": "Acknowledge completion",
        "continuation": "Resume previous goal",
        "correction": "Apply correction",
        "goal_creation": "Initialize new goal",
        "active_match": "Align with active goal",
        "embedding_match": "Align with related goal",
    }

    def __init__(
        self,
        pfc_config: "PFCIntegrationConfig | None" = None,
    ) -> None:
        # Phase 5 STEP 4 — PFC integration config (별도 필드, 기존 충돌 방지)
        from app.routing.pfc import PFCIntegrationConfig as _Cfg
        self._pfc_config: "PFCIntegrationConfig" = pfc_config or _Cfg()

    async def create_pre_plan(
        self,
        query: str,
        difficulty: int = 1,
        category: str | None = None,
        pfc_decision: "PFCDecision | None" = None,
    ) -> PrePlan:
        """Context 없이 임시 뼈대 생성.

        Phase 5 STEP 4 — PFC cue type 매트릭스:
          - completion/goal_creation/continuation/correction: PFC 강제 우선
          - active_match/embedding_match: confidence >= threshold → PFC, 미만 → regex
          - category_fallback/general_fallback: regex 우선
          - pfc_decision=None: Phase 4 STEP 5.2.5 패턴
        """
        intent = self._classify_intent_with_pfc(
            query=query, category=category, pfc_decision=pfc_decision,
        )
        steps_outline = self._generate_outline(
            intent=intent, difficulty=difficulty, pfc_decision=pfc_decision,
        )
        requires_context = intent in {"code_generation", "analysis"}
        # B12: one consistent "high difficulty" band = VERY_HARD(4)+ (matches the
        # deep-analysis outline, NE boost, and full_pipeline thresholds). Below
        # that the pre-plan skeleton is treated as the more-confident default.
        confidence = 0.6 if difficulty < 4 else 0.4
        return PrePlan(
            intent=intent,
            steps_outline=steps_outline,
            requires_context=requires_context,
            confidence=confidence,
        )

    async def inject_context(
        self,
        pre_plan: PrePlan,
        context_result: ContextAgentResult | None,
        query: str,
    ) -> FinalPlan:
        """Context 주입 후 final_plan 확정.

        context_result가 None이거나 비어있거나 전부 GABA-masked면
        context_used=False.
        """
        context_used = (
            context_result is not None
            and len(context_result.retrieved) > 0
            and any(not ctx.masked_by_gaba for ctx in context_result.retrieved)
        )

        context_chunk_ids: list[str] = []
        context_text_blocks: list[str] = []
        if context_used and context_result is not None:
            for ctx in context_result.retrieved:
                if not ctx.masked_by_gaba:
                    context_chunk_ids.append(ctx.chunk_id)
                    context_text_blocks.append(ctx.text)

        steps = list(pre_plan.steps_outline)
        pre_plan_modified = False
        if context_used and pre_plan.requires_context:
            steps.insert(0, "Review retrieved context")
            pre_plan_modified = True

        prompt_parts: list[str] = []
        if context_text_blocks:
            prompt_parts.append("[CONTEXT]")
            prompt_parts.extend(context_text_blocks)
            prompt_parts.append("[/CONTEXT]")
        prompt_parts.append(f"[QUERY] {query}")
        prompt_parts.append(f"[INTENT] {pre_plan.intent}")

        return FinalPlan(
            intent=pre_plan.intent,
            steps=steps,
            context_used=context_used,
            context_chunk_ids=context_chunk_ids,
            prompt_for_generator="\n".join(prompt_parts),
            pre_plan_modified=pre_plan_modified,
        )

    def _classify_intent(self, query: str, category: str | None = None) -> str:
        """휴리스틱 intent 분류 (Phase 4 STEP 5.2.5 패턴 — pfc_decision=None 경로).

        우선순위:
          1. regex 매칭 — 명시적 키워드가 있으면 regex 결과 사용.
          2. category fallback — regex가 general이면 Evaluator category 참조.
          3. general — category도 없거나 매핑 없으면 general.
        """
        regex_intent = self._classify_intent_by_regex(query)
        if regex_intent != "general":
            return regex_intent
        if category is not None:
            return self._CATEGORY_TO_INTENT.get(category, "general")
        return "general"

    def _classify_intent_with_pfc(
        self,
        query: str,
        category: str | None,
        pfc_decision: "PFCDecision | None",
    ) -> str:
        """PFC cue type 매트릭스 기반 intent 분류.

        pfc_decision=None 시 Phase 4 STEP 5.2.5 패턴과 100% 동일.
        """
        if pfc_decision is None:
            return self._classify_intent(query, category=category)

        cue_type = pfc_decision.hint.cue_type
        confidence = pfc_decision.hint.confidence

        if cue_type in {"completion", "goal_creation", "continuation", "correction"}:
            return self._pfc_intent_or_fallback(query, category, pfc_decision)

        if cue_type in {"active_match", "embedding_match"}:
            if confidence >= self._pfc_config.pfc_confidence_threshold:
                return self._pfc_intent_or_fallback(query, category, pfc_decision)
            return self._classify_intent(query, category=category)

        # category_fallback / general_fallback → regex 우선 (STEP 5.2.5 패턴)
        return self._classify_intent(query, category=category)

    def _pfc_intent_or_fallback(
        self,
        query: str,
        category: str | None,
        pfc_decision: "PFCDecision",
    ) -> str:
        """PFC decision의 goal context로 planner intent를 derive.

        - completion → "answer" (사용자가 끝났다고 알림, 확인/요약)
        - goal_creation → candidate.category → intent
        - continuation/correction → matched_goal.category → intent
        - active_match/embedding_match → matched_goal.category → intent
        - 위 정보가 없으면 regex/category fallback (STEP 5.2.5 패턴)
        """
        cue_type = pfc_decision.hint.cue_type

        if cue_type == "completion":
            return "answer"

        derived_category: str | None = None
        if cue_type == "goal_creation" and pfc_decision.new_goal_candidate is not None:
            derived_category = pfc_decision.new_goal_candidate.category
        elif pfc_decision.matched_goal is not None:
            derived_category = pfc_decision.matched_goal.category

        if derived_category is not None:
            return self._CATEGORY_TO_INTENT.get(derived_category, "general")

        # 정보 부족 → 기존 fallback 경로
        return self._classify_intent(query, category=category)

    def _classify_intent_by_regex(self, query: str) -> str:
        """순수 regex 기반 intent 분류 (category fallback 없음)."""
        lowered = query.lower()
        for intent, patterns in self._INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, lowered):
                    return intent
        return "general"

    def _generate_outline(
        self,
        intent: str,
        difficulty: int,
        pfc_decision: "PFCDecision | None" = None,
    ) -> list[str]:
        """intent + difficulty 기반 단계 템플릿 + PFC cue prefix."""
        steps = list(self._OUTLINE_TEMPLATES.get(intent, self._OUTLINE_TEMPLATES["general"]))
        # B12: 5-stage scale — prepend the deep-analysis step on the high-difficulty
        # band (VERY_HARD/DEEP_THINKING, >=4), matching the NE-boost / full_pipeline
        # threshold. Under the old 3-stage scale this fired at difficulty==3 (then
        # the top rung); 3 is now the middle, so the threshold moves to 4.
        if difficulty >= 4:
            steps = ["Deep analysis"] + steps
        if pfc_decision is not None:
            prefix = self._CUE_OUTLINE_PREFIX.get(pfc_decision.hint.cue_type)
            if prefix is not None:
                steps = [prefix] + steps
        return steps

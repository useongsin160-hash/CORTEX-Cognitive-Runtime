"""Async Swarm — Context + Planner + Generator 비동기 협업.

Micro-Sync 설계 (설계서 line 273-276):
  1. Context와 Planner는 병렬 실행
  2. 둘 다 완료 후 Planner.inject_context() 호출
  3. FinalPlan 확정 후에만 Generator 실행

원칙:
  - asyncio.gather(return_exceptions=True)로 병렬 + 예외 흡수
  - asyncio.CancelledError는 절대 삼키지 않고 re-raise (Teardown 대비)
  - 각 단계 실패 시 fallback으로 진행 — 시스템 다운 방지

Phase 5 STEP 4 — PFC 통합 (optional):
  - pfc 주입 시: Context와 PFC 병렬 시작 → PFC bounded wait → Planner(pfc_decision)
  - pfc=None 시: Phase 4 흐름 100% 보존 (gather 병렬)
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Final

from app.api.schemas.context import EvaluationResult, TaskContext
from app.api.schemas.query_features import QueryFeatures
from app.core.errors import LockTimeoutError
from app.core.logging import get_spinal_logger
from app.execution.context_agent import ContextAgent
from app.execution.context_models import ContextAgentResult
from app.execution.generator_agent import GeneratorAgent
from app.execution.plan_models import PrePlan
from app.execution.planner_agent import PlannerAgent
from app.execution.swarm_models import SwarmResult

if TYPE_CHECKING:
    from app.maintenance.plc import PLC
    from app.routing.pfc import PFCDecision, PFCIntegrationConfig, PrefrontalCortex

# Context retrieval timeout (초) — Emergent Deadlock 방어 (설계서 line 426).
CONTEXT_TIMEOUT_SECONDS: Final[float] = 5.0

# B11 S3b-demote: lightweight route skips Context Agent retrieval. Tuple shape
# matches _evaluate_context() output: (status, fallback_reason, context_for_inject).
_CONTEXT_SKIPPED: Final[tuple[str, str, None]] = (
    "skipped",
    "lightweight route — context retrieval skipped",
    None,
)


class AsyncSwarm:
    """Context + Planner + Generator를 Micro-Sync 순서로 협업 실행."""

    def __init__(
        self,
        context_agent: ContextAgent,
        planner_agent: PlannerAgent,
        generator_agent: GeneratorAgent,
        context_timeout: float = CONTEXT_TIMEOUT_SECONDS,
        plc: "PLC | None" = None,
        pfc: "PrefrontalCortex | None" = None,
        pfc_config: "PFCIntegrationConfig | None" = None,
    ) -> None:
        self._context_agent = context_agent
        self._planner_agent = planner_agent
        self._generator_agent = generator_agent
        self._context_timeout = context_timeout
        # Optional PLC for field-level protection (Phase 4 STEP 4).
        # None → backward-compat (all existing tests pass as-is).
        self._plc = plc
        # Phase 5 STEP 4 — optional PFC integration.
        # None → Phase 4 흐름 100% 보존.
        self._pfc = pfc
        self._pfc_config: "PFCIntegrationConfig | None" = pfc_config
        if pfc is not None and self._pfc_config is None:
            from app.routing.pfc import PFCIntegrationConfig as _Cfg
            self._pfc_config = _Cfg()
        # background tasks (late PFC handlers) — leak 방지를 위해 strong ref 유지
        self._background_tasks: set[asyncio.Task] = set()

    async def execute(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None = None,
    ) -> SwarmResult:
        """Swarm 실행. pfc=None 시 Phase 4 경로, 주입 시 Phase 5 경로."""
        if self._pfc is None:
            return await self._execute_phase4(task_context, query_features)
        return await self._execute_with_pfc(task_context, query_features)

    # ------------------------------------------------------------------
    # Phase 4 경로 (pfc=None) — 기존 흐름 100% 보존
    # ------------------------------------------------------------------

    async def _execute_phase4(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None,
    ) -> SwarmResult:
        total_start = time.perf_counter()

        if task_context.route_path == "lightweight":
            # B11 S3b-demote: lightweight skips Context Agent retrieval (ChromaDB
            # 0). Planner runs alone; inject_context handles a None context.
            pre_plan_start = time.perf_counter()
            try:
                raw_pre_plan: object = await self._planner_agent.create_pre_plan(
                    query=task_context.prompt or "",
                    difficulty=int(task_context.difficulty),
                    category=task_context.category,
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                raw_pre_plan = exc
            pre_plan_elapsed = (time.perf_counter() - pre_plan_start) * 1000
            context_elapsed = 0.0
            context_status, context_fallback_reason, context_for_inject = (
                _CONTEXT_SKIPPED
            )
        else:
            # 1. Context + Planner 병렬 실행.
            raw_context, raw_pre_plan, context_elapsed, pre_plan_elapsed = (
                await self._parallel_context_and_planner(
                    task_context=task_context,
                    query_features=query_features,
                )
            )

            # 2. Context 상태 판단.
            context_status, context_fallback_reason, context_for_inject = (
                self._evaluate_context(raw_context)
            )

        # 3. Planner 상태 판단.
        planner_status, pre_plan = self._evaluate_pre_plan(raw_pre_plan)

        # 3a. Write context result to TaskContext under PLC protection.
        if context_for_inject is not None:
            await self._write_context_result(task_context, context_for_inject)

        # 4. inject_context — final_plan 생성.
        inject_start = time.perf_counter()
        final_plan = await self._planner_agent.inject_context(
            pre_plan=pre_plan,
            context_result=context_for_inject,
            query=task_context.prompt or "",
        )
        final_plan = final_plan.model_copy(update={
            "context_status": context_status,
            "context_fallback_reason": context_fallback_reason,
        })
        inject_elapsed = (time.perf_counter() - inject_start) * 1000

        # 5. Generator 실행.
        generator_start = time.perf_counter()
        generator_result = await self._generator_agent.generate(
            final_plan=final_plan,
            task_context=task_context,
        )
        generator_elapsed = (time.perf_counter() - generator_start) * 1000

        generator_status = (
            "ok" if generator_result.finish_reason == "stop" else "fallback"
        )
        total_elapsed = (time.perf_counter() - total_start) * 1000

        return SwarmResult(
            context_result=context_for_inject,
            final_plan=final_plan,
            generator_result=generator_result,
            context_status=context_status,
            planner_status=planner_status,
            generator_status=generator_status,
            context_elapsed_ms=context_elapsed,
            pre_plan_elapsed_ms=pre_plan_elapsed,
            inject_elapsed_ms=inject_elapsed,
            generator_elapsed_ms=generator_elapsed,
            total_elapsed_ms=total_elapsed,
        )

    async def _parallel_context_and_planner(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None,
    ) -> tuple[object, object, float, float]:
        """Context(timeout 적용) + Planner를 gather로 병렬 실행."""
        start = time.perf_counter()

        context_coro = asyncio.wait_for(
            self._context_agent.retrieve(
                task_context=task_context,
                query_features=query_features,
            ),
            timeout=self._context_timeout,
        )
        pre_plan_coro = self._planner_agent.create_pre_plan(
            query=task_context.prompt or "",
            difficulty=int(task_context.difficulty),
            category=task_context.category,
        )

        results = await asyncio.gather(
            context_coro,
            pre_plan_coro,
            return_exceptions=True,
        )
        elapsed = (time.perf_counter() - start) * 1000

        context_result, pre_plan = results

        if isinstance(context_result, asyncio.CancelledError):
            raise context_result
        if isinstance(pre_plan, asyncio.CancelledError):
            raise pre_plan

        return context_result, pre_plan, elapsed, elapsed

    # ------------------------------------------------------------------
    # Phase 5 경로 (pfc 주입) — Context‖PFC 병렬 → Planner → Context join
    # ------------------------------------------------------------------

    async def _execute_with_pfc(
        self,
        task_context: TaskContext,
        query_features: QueryFeatures | None,
    ) -> SwarmResult:
        assert self._pfc is not None
        assert self._pfc_config is not None

        total_start = time.perf_counter()
        logger = get_spinal_logger()

        # 1. Context와 PFC를 병렬로 시작 (B11 S3b-demote: lightweight면 Context 미생성)
        lightweight = task_context.route_path == "lightweight"
        context_start = time.perf_counter()
        context_task: asyncio.Task | None = None
        if not lightweight:
            context_task = asyncio.create_task(
                asyncio.wait_for(
                    self._context_agent.retrieve(
                        task_context=task_context,
                        query_features=query_features,
                    ),
                    timeout=self._context_timeout,
                )
            )
        pfc_task = asyncio.create_task(self._execute_pfc(task_context))

        # 2. PFC bounded wait (shield 패턴)
        pfc_decision = await self._await_pfc_bounded(
            pfc_task=pfc_task,
            context_task=context_task,
            task_context=task_context,
            logger=logger,
        )

        # 3. Planner 시작 (PFC decision과 함께)
        pre_plan_start = time.perf_counter()
        try:
            raw_pre_plan: object = await self._planner_agent.create_pre_plan(
                query=task_context.prompt or "",
                difficulty=int(task_context.difficulty),
                category=task_context.category,
                pfc_decision=pfc_decision,
            )
        except asyncio.CancelledError:
            await self._cleanup_tasks(pfc_task=pfc_task, context_task=context_task)
            raise
        except BaseException as exc:
            raw_pre_plan = exc
        pre_plan_elapsed = (time.perf_counter() - pre_plan_start) * 1000

        planner_status, pre_plan = self._evaluate_pre_plan(raw_pre_plan)

        # 4. Context 완료 대기 (lightweight면 스킵 — ChromaDB 0)
        if lightweight:
            context_elapsed = 0.0
            context_status, context_fallback_reason, context_for_inject = (
                _CONTEXT_SKIPPED
            )
        else:
            raw_context: object
            try:
                raw_context = await context_task
            except asyncio.CancelledError:
                await self._cleanup_tasks(pfc_task=pfc_task, context_task=None)
                raise
            except BaseException as exc:
                raw_context = exc
            context_elapsed = (time.perf_counter() - context_start) * 1000

            context_status, context_fallback_reason, context_for_inject = (
                self._evaluate_context(raw_context)
            )

        if context_for_inject is not None:
            await self._write_context_result(task_context, context_for_inject)

        # 5. inject_context
        inject_start = time.perf_counter()
        final_plan = await self._planner_agent.inject_context(
            pre_plan=pre_plan,
            context_result=context_for_inject,
            query=task_context.prompt or "",
        )
        final_plan = final_plan.model_copy(update={
            "context_status": context_status,
            "context_fallback_reason": context_fallback_reason,
        })
        inject_elapsed = (time.perf_counter() - inject_start) * 1000

        # 6. Generator
        generator_start = time.perf_counter()
        generator_result = await self._generator_agent.generate(
            final_plan=final_plan,
            task_context=task_context,
        )
        generator_elapsed = (time.perf_counter() - generator_start) * 1000

        generator_status = (
            "ok" if generator_result.finish_reason == "stop" else "fallback"
        )
        total_elapsed = (time.perf_counter() - total_start) * 1000

        return SwarmResult(
            context_result=context_for_inject,
            final_plan=final_plan,
            generator_result=generator_result,
            context_status=context_status,
            planner_status=planner_status,
            generator_status=generator_status,
            context_elapsed_ms=context_elapsed,
            pre_plan_elapsed_ms=pre_plan_elapsed,
            inject_elapsed_ms=inject_elapsed,
            generator_elapsed_ms=generator_elapsed,
            total_elapsed_ms=total_elapsed,
        )

    async def _execute_pfc(self, task_context: TaskContext) -> "PFCDecision":
        """PFC reasoning 호출. TaskContext에서 EvaluationResult를 합성.

        Phase 5 STEP 4 — minimal input. active_goal과 goal_stack_summary는
        STEP 5에서 GoalStack 통합 시 채워짐.
        """
        eval_result = EvaluationResult(
            difficulty=int(task_context.difficulty),
            category=task_context.category or "general",
            embedding=[],
            confidence=0.5,
            similarity=0.0,
        )
        return await self._pfc.infer_hint(
            query=task_context.prompt or "",
            eval_result=eval_result,
            goal_stack_summary=None,
            active_goal=None,
        )

    async def _await_pfc_bounded(
        self,
        pfc_task: asyncio.Task,
        context_task: asyncio.Task | None,
        task_context: TaskContext,
        logger,
    ) -> "PFCDecision | None":
        """Bounded wait on PFC with shield + timeout + late handler.

        CancelledError는 절대 삼키지 않는다. 일반 예외는 흡수 후 pfc.error 기록.
        Timeout 시 late handler 등록 후 pfc_hint=None으로 진행.
        """
        timeout_s = self._pfc_config.hint_timeout_ms / 1000.0
        try:
            decision = await asyncio.wait_for(
                asyncio.shield(pfc_task),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            await logger.log_event(
                trace_id=task_context.trace_id,
                module_name="execution.swarm",
                event_type="pfc.timeout",
                payload={"timeout_ms": self._pfc_config.hint_timeout_ms},
            )
            self._spawn_late_handler(pfc_task, task_context)
            return None
        except asyncio.CancelledError:
            # outer cancellation: pfc_task와 context_task 모두 정리 후 re-raise
            await self._cleanup_tasks(pfc_task=pfc_task, context_task=context_task)
            raise
        except BaseException as exc:
            # PFC 일반 예외 — pfc.error 기록 후 pfc_hint=None
            await logger.log_event(
                trace_id=task_context.trace_id,
                module_name="execution.swarm",
                event_type="pfc.error",
                payload={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return None

        # 정상 완료
        await logger.log_event(
            trace_id=task_context.trace_id,
            module_name="execution.swarm",
            event_type="pfc.completed",
            payload={
                "cue_type": decision.hint.cue_type,
                "intent": decision.hint.intent,
                "confidence": decision.hint.confidence,
                "matched_goal_id": decision.hint.matched_goal_id,
            },
        )
        return decision

    def _spawn_late_handler(
        self,
        pfc_task: asyncio.Task,
        task_context: TaskContext,
    ) -> None:
        """Timeout 후 PFC가 끝나면 SpinalLogger에 기록하는 background task."""
        late_task = asyncio.create_task(
            self._handle_late_pfc(pfc_task, task_context)
        )
        self._background_tasks.add(late_task)
        late_task.add_done_callback(self._background_tasks.discard)

    async def _handle_late_pfc(
        self,
        pfc_task: asyncio.Task,
        task_context: TaskContext,
    ) -> None:
        """Late PFC continuation — SpinalLogger 기록만, GoalStack mutation 금지.

        Phase 5 STEP 4 규약:
          - SpinalLogger에 pfc.late_completion / pfc.late_error만 기록
          - GoalStack mutation 0건 (STEP 5 또는 후속)
          - 예외 발생해도 swarm 전체에 영향 없음 (background)
        """
        logger = get_spinal_logger()
        try:
            decision = await pfc_task
        except asyncio.CancelledError:
            # outer cancel로 정리된 경우 graceful 종료
            return
        except BaseException as exc:
            try:
                await logger.log_event(
                    trace_id=task_context.trace_id,
                    module_name="execution.swarm",
                    event_type="pfc.late_error",
                    payload={
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
            except Exception:
                pass  # background 로깅 실패도 swallow
            return

        try:
            await logger.log_event(
                trace_id=task_context.trace_id,
                module_name="execution.swarm",
                event_type="pfc.late_completion",
                payload={
                    "cue_type": decision.hint.cue_type,
                    "intent": decision.hint.intent,
                    "confidence": decision.hint.confidence,
                    "matched_goal_id": decision.hint.matched_goal_id,
                },
            )
        except Exception:
            pass

    async def _cleanup_tasks(
        self,
        pfc_task: asyncio.Task | None,
        context_task: asyncio.Task | None,
    ) -> None:
        """outer CancelledError 시 pfc_task와 context_task 모두 정리.

        zombie task 방지. 정리 중 예외는 흡수 (re-raise는 호출자 책임).
        """
        for task in (pfc_task, context_task):
            if task is None or task.done():
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass

    # ------------------------------------------------------------------
    # 공통 헬퍼 (Phase 4와 Phase 5 경로 공유)
    # ------------------------------------------------------------------

    def _evaluate_context(
        self,
        context_result: object,
    ) -> tuple[str, str | None, ContextAgentResult | None]:
        """Context 결과 검사 → (status, fallback_reason, context_for_inject)."""
        if isinstance(context_result, asyncio.TimeoutError):
            return "timeout", "Context retrieval exceeded timeout", None

        if isinstance(context_result, BaseException):
            return (
                "error",
                f"Context retrieval failed: {type(context_result).__name__}",
                None,
            )

        assert isinstance(context_result, ContextAgentResult)

        if not context_result.retrieved:
            return "empty", "No context retrieved", context_result

        if all(ctx.masked_by_gaba for ctx in context_result.retrieved):
            if context_result.gaba_fallback_used:
                return "ok", None, context_result
            return "empty", "All context masked by GABA", context_result

        return "ok", None, context_result

    async def _write_context_result(
        self,
        task_context: TaskContext,
        context_result: ContextAgentResult,
    ) -> None:
        """Write context result to TaskContext under PLC protection."""
        if self._plc is None:
            task_context.context_agent_result = context_result
            return

        try:
            async with self._plc.protect_context_update(task_context.trace_id):
                task_context.context_agent_result = context_result
        except asyncio.CancelledError:
            raise
        except LockTimeoutError:
            task_context.context_agent_result = context_result

    def _evaluate_pre_plan(
        self,
        pre_plan: object,
    ) -> tuple[str, PrePlan]:
        """Pre-plan 검사 → (status, PrePlan). 예외면 fallback pre_plan."""
        if isinstance(pre_plan, asyncio.CancelledError):
            raise pre_plan

        if isinstance(pre_plan, BaseException):
            return "fallback", PrePlan(
                intent="general",
                steps_outline=["Process query", "Generate response"],
                requires_context=False,
                confidence=0.3,
            )

        assert isinstance(pre_plan, PrePlan)
        return "ok", pre_plan

"""Phase 2 ingress + routing pipeline.

Strict order (per design sequence steps 1–8):
    1. PromptSanitizer
    1.5 Glycine pre-flight
    1.6 ContinuationDetector (Phase 5 STEP 5) — if bypass, jump to step 5
    2. Thalamus
    3. Tier-1 Exact Cache
    4. Tier-2 Semantic Cache (similarity preserved for Tier-1.5)
    5. Semantic Evaluator
    6. LC (finalize difficulty + build TaskContext)
    7. Tier-1.5 — serial branch AFTER LC only
    8. Skip Router
    9. PHASE 3+: replace stub with real execution layer

Phase 5 STEP 5: continuation cue + active_goal 결합 시 Thalamus/Cache/Tier-1.5를
우회하고 직접 Evaluator → LC → AsyncSwarm으로 분기한다. Sanitizer/Glycine은 절대
우회하지 않는다. continuation bypass 응답은 ExactCache/SemanticCache에 저장 금지.

Components live on `app.state` (initialized in app/main.py:create_app).
Every early-return path emits a Spinal trace event before returning.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.context import ContinuationContext
from app.api.schemas.query_features import QueryFeatures
from app.api.schemas.request import QueryRequest
from app.api.schemas.response import HealthResponse, QueryResponse, SwarmTrace
from app.basal_ganglia.advisor import (
    build_action_selection_context_from_snapshots,
    route_path_for_candidate_type,
)
from app.core.errors import ValidationError
from app.core.logging import get_spinal_logger
from app.core.slot_registry import slots_ready as compute_slots_ready
from app.routing.pfc import make_goal_stack_summary
from app.routing.skip_router import RouteDecision

# Physical route bands, low → high (mirrors routing_ratchet/rpe_route_override/
# crossroad's private _BANDS; kept local to avoid importing a private symbol). Used
# only for the BG-apply promote-only clamp — BG may raise route_path along this
# ladder, never lower it.
_ROUTE_BANDS: tuple[str, ...] = ("lightweight", "standard", "full_pipeline")


def _band_index(path: str | None) -> int:
    """Ladder index of a route band; unknown/None → -1 (below the ladder)."""
    try:
        return _ROUTE_BANDS.index(path)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return -1

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Read-only 상태 노출 — live 게이트(llm_mode) + 슬롯 준비(slots_ready).

    인지 파이프라인 바깥의 순수 조회다: SpinalLogger trace / RPE 관측 / 인지
    로그를 남기지 않으므로 readiness 폴링으로 빈번히 호출돼도 로그가 오염되지
    않는다. slots_ready 는 slot_registry preflight(check_llm_slots 와 공유)를
    재사용하며, 키 값·env 이름·벤더명을 응답에 싣지 않는다.
    """
    return HealthResponse(
        status="ok",
        version="0.1.0",
        llm_mode=_state_llm_mode(request.app.state),
        slots_ready=compute_slots_ready(),
    )


@router.post("/query", response_model=QueryResponse)
async def query(payload: QueryRequest, request: Request) -> QueryResponse:
    state = request.app.state
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    # Phase 3.5: Synapse is session-scoped. Fall back to trace_id when the
    # caller supplies no session_id (real session mgmt arrives in Phase 5).
    session_id = payload.session_id or trace_id

    await logger.log_event(
        trace_id=trace_id,
        module_name="api.routes",
        event_type="query.received",
        payload={"prompt_len": len(payload.prompt), "session_id": payload.session_id},
    )

    # ── 1. PromptSanitizer ────────────────────────────────────────────────
    try:
        await state.sanitizer.sanitize(payload.prompt, trace_id=trace_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ── 1.5 Glycine pre-flight hard brake ────────────────────────────────
    session_key = payload.session_id or trace_id
    glycine_decision = await state.glycine.check_pre_flight(
        prompt=payload.prompt, session_key=session_key,
    )
    if glycine_decision.active:
        await logger.log_event(
            trace_id=trace_id,
            module_name="routing.glycine",
            event_type="glycine.blocked",
            payload={"reason": glycine_decision.reason, "action": glycine_decision.action},
        )
        return await _complete(
            logger, trace_id,
            path_taken="glycine_blocked",
            answer=f"[GLYCINE BLOCKED] {glycine_decision.reason}",
            response_source="fallback",
            glycine_active=True,
            glycine_reason=glycine_decision.reason,
            glycine_action=glycine_decision.action,
        )

    # ── 1.6 Continuation Detector (Phase 5 STEP 5) ────────────────────────
    # continuation cue + active_goal 결합 시 Thalamus/Cache/Tier-1.5를 우회한다.
    # Sanitizer/Glycine은 이미 통과 — 우회 대상이 아니다.
    # session_id 없거나 active_goal 없거나 detector 실패 시 normal path fail-open.
    continuation_decision = None
    detector = getattr(state, "continuation_detector", None)
    if detector is not None:
        continuation_decision = await detector.detect(
            query=payload.prompt,
            session_id=payload.session_id,
            trace_id=trace_id,
        )
        if continuation_decision.should_bypass:
            return await _continuation_bypass_path(
                state=state,
                logger=logger,
                trace_id=trace_id,
                session_id=session_id,
                payload=payload,
                continuation_snapshot=continuation_decision.active_goal_snapshot,
            )

    # ── 2. Thalamus reflex ────────────────────────────────────────────────
    hit, reflex_reply = await state.thalamus.should_short_circuit(payload.prompt)
    if hit and reflex_reply is not None:
        await logger.log_event(
            trace_id=trace_id,
            module_name="ingress.thalamus",
            event_type="thalamus.hit",
            payload={"reply_len": len(reflex_reply)},
        )
        return await _complete(
            logger, trace_id, path_taken="thalamus", answer=reflex_reply,
            response_source="thalamus",
        )

    # ── 3. Tier-1 Exact Cache ────────────────────────────────────────────
    # 캐시 read 는 LC tier 선택(step 6) 이전이라 resolved slot/model 을 모른다 →
    # slot_fingerprint/model_id 는 unresolved(기본 None). llm_mode 만 네임스페이스에
    # 실어 mock 시대 답변을 live 가 hit 하지 못하게 한다(read-side hardening, A1).
    exact_hit = await state.exact_cache.get(
        payload.prompt, llm_mode=_state_llm_mode(state),
    )
    if exact_hit is not None:
        await logger.log_event(
            trace_id=trace_id,
            module_name="ingress.exact_cache",
            event_type="exact_cache.hit",
            payload={},
        )
        return await _complete(
            logger, trace_id, path_taken="exact_cache", answer=exact_hit,
            response_source="exact_cache",
        )

    # ── 4. Tier-2 Semantic Cache (similarity preserved for Tier-1.5) ─────
    # threshold=0.0 forces the top-1 result regardless of band, so we can
    # both (a) honor Tier-2's 0.90 cutoff and (b) keep the sub-0.90 value
    # alive for the Tier-1.5 decision at step 7.
    # llm_mode 네임스페이스 동일 적용(read-side hardening, A1). slot/model 은
    # tier 선택 전이라 unresolved.
    semantic_top = await state.semantic_cache.get(
        payload.prompt, threshold=0.0, llm_mode=_state_llm_mode(state),
    )
    tier15_similarity: float | None = None
    tier15_cached_response: str | None = None
    if semantic_top is not None:
        cached_response, similarity = semantic_top
        if similarity >= 0.90:
            await logger.log_event(
                trace_id=trace_id,
                module_name="ingress.semantic_cache",
                event_type="semantic_cache.hit",
                payload={"similarity": similarity},
            )
            return await _complete(
                logger, trace_id, path_taken="semantic_cache", answer=cached_response,
                response_source="semantic_cache",
            )
        tier15_similarity = similarity
        tier15_cached_response = cached_response
        await logger.log_event(
            trace_id=trace_id,
            module_name="ingress.semantic_cache",
            event_type="semantic_cache.below_threshold",
            payload={"similarity": similarity},
        )

    # ── 5. Semantic Evaluator (must precede LC per design step 5) ────────
    # trace_id is forwarded so the evaluator can emit Graceful Fallback
    # warnings (evaluator.fallback) if CentroidStore misfires.
    evaluation = await state.evaluator.evaluate(payload.prompt, trace_id=trace_id)
    await logger.log_event(
        trace_id=trace_id,
        module_name="routing.semantic_evaluator",
        event_type="evaluator.classified",
        payload={
            "difficulty": evaluation.difficulty,
            "category": evaluation.category,
            "confidence": evaluation.confidence,
            "similarity": evaluation.similarity,
            "classification_method": evaluation.classification_method,
        },
    )

    # ── 5.1 Synapse Observe — record the Evaluator result, never reroute ─
    # Observe runs only on the post-Evaluator path; the thalamus/cache
    # early-exits return before this point and never touch Synapse.
    await state.synapse_observer.observe(
        session_id=session_id,
        category=evaluation.category,
        embedding=evaluation.embedding,
        similarity=evaluation.similarity,
        trace_id=trace_id,
    )

    # ── 6. LC — finalize difficulty + TaskContext, dispatch PFC async ────
    task_context = await state.lc.process(payload.prompt, evaluation, trace_id=trace_id)

    # ── 7. Tier-1.5 — STRICTLY after LC, serial branch (no parallelism) ──
    # Tier-1.5 is an early-exit path: per spec correction 5, leave the
    # selected_tier fields at None so the public response distinguishes
    # the LC-routed bucket from the early-exit bucket.
    if await state.tier15.should_activate(task_context, tier15_similarity):
        # PHASE 3: replace with real Flash model "diff-edit" call.
        answer = await state.tier15.execute(
            payload.prompt, tier15_cached_response or "",
        )
        await logger.log_event(
            trace_id=trace_id,
            module_name="routing.tier1_5",
            event_type="tier1_5.executed",
            payload={"similarity": tier15_similarity},
        )
        return await _complete(
            logger, trace_id,
            path_taken="tier_1_5",
            answer=answer,
            response_source="tier_1_5",
            difficulty=int(task_context.difficulty),
            category=evaluation.category,
        )

    # ── 7.1 Synapse Snapshot — Tier-1.5 MISS only ───────────────────────
    # Reaching here means Tier-1.5 did not fire; stamp the current weight
    # map onto the TaskContext. Early-exit paths skip this entirely so
    # their synapse_snapshot stays an empty dict.
    await state.lc.apply_snapshot(task_context, session_id, trace_id)

    # ── 7.15 RPE step decay (B11 S5) — realize the current cell's accrued idle
    # decay BEFORE the gate/override read it, so a forgotten cell can demote this
    # request (releases the S4 ratchet floor toward baseline). Lazy / O(1).
    await state.routing_decay.step(task_context, session_id)

    # ── 7.2 RPE difficulty gate (B11 S2) — overlay the learned (category,
    # difficulty) cell onto the snapshot before routing so CategorySelector →
    # ContextAgent reflects learned focus. Read-only; unlearned cell = no-op.
    await state.synapse_difficulty_gate.overlay(task_context, session_id)

    # ── 8. Skip Router ───────────────────────────────────────────────────
    decision = await state.skip_router.route(task_context)
    await logger.log_event(
        trace_id=trace_id,
        module_name="routing.skip_router",
        event_type="skip_router.decided",
        payload={
            "path": decision.path,
            "skip_layers": decision.skip_layers,
            "reason": decision.reason,
        },
    )

    # ── 8.1 RPE biological routing override (B11 S3a) — label only. Learned
    # (category, difficulty) weight shifts the path band ±1; unlearned = no-op
    # (B12 path stands). tier unchanged.
    decision = await state.rpe_route_override.apply(decision, task_context, session_id)
    # B11 S4 — monotonic ratchet: clamp the path up to the session floor (no demote
    # once promoted); the floor rises with the final path. Demotion now comes only
    # from S5 decay (rise=learning, fall=forgetting).
    decision = await state.routing_ratchet.apply(decision, task_context, session_id)
    # B11 S3b — stamp the final path so the swarm wires execution to the band
    # (lightweight skips Context Agent retrieval).
    task_context.route_path = decision.path
    # B11 S3b-promote — Epinephrine redefined: active iff the final path is
    # full_pipeline (난이도 무관 — RPE-promote된 난이도 2·3도 포함). This drives the
    # ContextAgent limit-break (broader category scope). tier·경로 결정 불변.
    _is_limit_break = decision.path == "full_pipeline"
    task_context.epinephrine_active = _is_limit_break
    task_context.epinephrine_reason = "limit_break" if _is_limit_break else None

    # ── 8.2 BasalGanglia apply (C2, promote-only) + B7/B10 signal plumbing. Run PFC
    # once (LLM-free, with goal context) → real pfc/lc/rpe signals for BG, and the
    # pfc_explore signal reused by CR at 9.1. With bg_apply_enabled, BG may PROMOTE
    # route_path (never demote — ratchet/baseline already stamped above); the
    # returned decision is threaded into CR so it sees the same (promoted) path.
    bg_pfc_decision = await _run_pfc_decision(
        state, task_context, evaluation, session_id,
    )
    decision = await _basal_ganglia_apply(
        state, task_context, decision, bg_pfc_decision,
        trace_id=trace_id, session_id=session_id,
    )

    # ── 9. Routed execution — AsyncSwarm 실행 후 generator text를 answer로 surface ─
    # PHASE 3.5: Synapse Layer weight lookup must run here before agents.
    # PHASE 4: Async Swarm (Context + Planner + Generator + Basal Ganglia).
    # PHASE 5: PFC Goal Stack injected into Planner.
    # PHASE 6: Dopamine RPE → Synapse weight feedback after CP3 verdict.
    # live LLM answer path: "Phase 2 stub ..." 합성 문자열 제거 — answer는
    # swarm 실행 후 _answer_from_swarm(swarm_result)에서 도출한다.
    tier_name = task_context.selected_tier.name
    eph_active = task_context.epinephrine_active
    # Phase 4 STEP 3.3b — routed path만 AsyncSwarm.execute() 호출.
    # ADR-002 부분 활용: SemanticEvaluator가 이미 계산한 embedding을
    # QueryFeatures.embedding 슬롯에 재주입하여 Context Agent가 재계산하지 않음.
    query_features = QueryFeatures(
        raw_query=payload.prompt,
        embedding=evaluation.embedding or None,
        category=evaluation.category,
        difficulty=int(task_context.difficulty),
        similarity=evaluation.similarity,
        embedding_source="evaluator" if evaluation.embedding else None,
    )
    # Phase 6 STEP 3.2: rpe_pipeline wraps the inner swarm and optionally
    # fires a background RPE task after the response is ready. trace_id and
    # session_id are consumed by the wrapper to build RPEPipelineSnapshot;
    # SwarmResult is returned unchanged.
    swarm_result = await state.rpe_pipeline.execute(
        task_context=task_context,
        query_features=query_features,
        trace_id=trace_id,
        session_id=session_id,
    )
    # ── 9.1 Crossroad Reasoning (B8) — at a near-tie route-band crossroad, stable
    # mode may fire a background explore of the adjacent band. The response below
    # is the #1 band's; the explore is learning-only. Frozen (cr_enabled=False);
    # the await covers only the sync decision, never the background explore swarm.
    await state.crossroad.maybe_explore(
        task_context, decision, session_id,
        query_features=query_features,
        pfc_explore=_pfc_explore_signal(bg_pfc_decision),
    )
    swarm_trace = _swarm_result_to_trace(swarm_result)
    answer, answer_source = _answer_from_swarm(swarm_result)
    await logger.log_event(
        trace_id=trace_id,
        module_name="execution.swarm",
        event_type="swarm.executed",
        payload={
            "status": swarm_trace.status,
            "elapsed_ms": swarm_trace.elapsed_ms,
            "plan_intent": swarm_trace.plan_intent,
            "answer_source": answer_source,
        },
    )

    return await _complete(
        logger, trace_id,
        path_taken=f"routed_{decision.path}",
        answer=answer,
        response_source="swarm",
        answer_source=answer_source,
        llm_mode=_state_llm_mode(state),
        route_decision=decision,
        difficulty=int(task_context.difficulty),
        category=evaluation.category,
        selected_tier=tier_name,
        epinephrine_active=eph_active,
        epinephrine_reason=task_context.epinephrine_reason,
        swarm_trace=swarm_trace,
    )


async def _continuation_bypass_path(
    *,
    state,
    logger,
    trace_id: str,
    session_id: str,
    payload: QueryRequest,
    continuation_snapshot: ContinuationContext | None,
) -> QueryResponse:
    """Phase 5 STEP 5 — Continuation forced routed/swarm path.

    Thalamus / ExactCache / SemanticCache / Tier-1.5 read 및 write 모두 우회.
    Sanitizer / Glycine은 이미 routes 본문에서 호출됨.
    응답 캐싱 금지 — cache key에 session_id/active_goal_id가 없으므로 cross-session
    오염 위험.
    """
    # ── 5. Semantic Evaluator (필수 — TaskContext 구성에 필요)
    evaluation = await state.evaluator.evaluate(payload.prompt, trace_id=trace_id)
    await logger.log_event(
        trace_id=trace_id,
        module_name="routing.semantic_evaluator",
        event_type="evaluator.classified",
        payload={
            "difficulty": evaluation.difficulty,
            "category": evaluation.category,
            "confidence": evaluation.confidence,
            "similarity": evaluation.similarity,
            "classification_method": evaluation.classification_method,
        },
    )

    # ── 5.1 Synapse Observe (정상 경로와 동일)
    await state.synapse_observer.observe(
        session_id=session_id,
        category=evaluation.category,
        embedding=evaluation.embedding,
        similarity=evaluation.similarity,
        trace_id=trace_id,
    )

    # ── 6. LC — finalize difficulty + TaskContext
    task_context = await state.lc.process(payload.prompt, evaluation, trace_id=trace_id)

    # continuation forced route: difficulty 최소 2 보정, active_goal_category 우선,
    # continuation_context 탑재. selected_tier / epinephrine / norepinephrine은
    # LC 규약 그대로 유지.
    forced_difficulty = max(int(task_context.difficulty), 2)
    forced_category = (
        continuation_snapshot.active_goal_category
        if continuation_snapshot and continuation_snapshot.active_goal_category
        else evaluation.category
    )
    task_context = task_context.model_copy(update={
        "category": forced_category,
        "difficulty": forced_difficulty,
        "continuation_context": continuation_snapshot,
    })

    # ── 7. Tier-1.5 강제 skip (continuation bypass 규약)
    await logger.log_event(
        trace_id=trace_id,
        module_name="routing.continuation_detector",
        event_type="continuation.cache_bypassed",
        payload={
            "active_goal_id": (
                continuation_snapshot.active_goal_id if continuation_snapshot else None
            ),
        },
    )

    # ── 7.1 Synapse Snapshot — Tier-1.5 우회 케이스이므로 정상 경로와 동일하게 스냅
    await state.lc.apply_snapshot(task_context, session_id, trace_id)

    # ── 7.15 RPE step decay (B11 S5) — continuation 경로도 동일하게 idle 칸 망각 실현.
    await state.routing_decay.step(task_context, session_id)

    # ── 7.2 RPE difficulty gate (B11 S2) — continuation path도 정상 경로와 동일하게
    # 학습된 (category, difficulty) 칸을 snapshot에 오버레이(읽기 전용, 미학습=no-op).
    await state.synapse_difficulty_gate.overlay(task_context, session_id)

    # ── 8. Skip Router
    decision = await state.skip_router.route(task_context)
    await logger.log_event(
        trace_id=trace_id,
        module_name="routing.skip_router",
        event_type="skip_router.decided",
        payload={
            "path": decision.path,
            "skip_layers": decision.skip_layers,
            "reason": decision.reason,
        },
    )

    # ── 8.1 RPE biological routing override (B11 S3a) — continuation 경로도 동일.
    decision = await state.rpe_route_override.apply(decision, task_context, session_id)
    # B11 S4 — continuation 경로도 동일 ratchet 클램프(세션 내 강등 금지).
    decision = await state.routing_ratchet.apply(decision, task_context, session_id)
    # B11 S3b — continuation 경로도 final path를 swarm에 전달(lightweight=Context 스킵).
    task_context.route_path = decision.path
    # B11 S3b-promote — continuation 경로도 동일하게 에피네프린 재정의(limit-break).
    _is_limit_break = decision.path == "full_pipeline"
    task_context.epinephrine_active = _is_limit_break
    task_context.epinephrine_reason = "limit_break" if _is_limit_break else None

    # ── 8.2 BasalGanglia apply (C2, promote-only) + B7/B10 signal plumbing. Run PFC
    # once (LLM-free, with goal context) → real pfc/lc/rpe signals for BG, and the
    # pfc_explore signal reused by CR at 9.1. With bg_apply_enabled, BG may PROMOTE
    # route_path (never demote — ratchet/baseline already stamped above); the
    # returned decision is threaded into CR so it sees the same (promoted) path.
    bg_pfc_decision = await _run_pfc_decision(
        state, task_context, evaluation, session_id,
    )
    decision = await _basal_ganglia_apply(
        state, task_context, decision, bg_pfc_decision,
        trace_id=trace_id, session_id=session_id,
    )

    # ── 9. AsyncSwarm 직접 호출 (continuation forced swarm)
    # live LLM answer path: 합성 "Phase 2 stub ..." 제거 — answer는 swarm 실행 후
    # _answer_from_swarm(swarm_result)에서 도출한다 (정상 routed path와 동일 규약).
    tier_name = task_context.selected_tier.name
    eph_active = task_context.epinephrine_active
    query_features = QueryFeatures(
        raw_query=payload.prompt,
        embedding=evaluation.embedding or None,
        category=forced_category,
        difficulty=int(task_context.difficulty),
        similarity=evaluation.similarity,
        embedding_source="evaluator" if evaluation.embedding else None,
    )
    # Phase 6 STEP 3.2: rpe_pipeline wraps the inner swarm in the
    # continuation bypass path too. SwarmResult returned unchanged.
    swarm_result = await state.rpe_pipeline.execute(
        task_context=task_context,
        query_features=query_features,
        trace_id=trace_id,
        session_id=session_id,
    )
    # ── 9.1 Crossroad Reasoning (B8) — continuation path fires the same near-tie
    # band explore (background, learning-only; frozen cr_enabled=False).
    await state.crossroad.maybe_explore(
        task_context, decision, session_id,
        query_features=query_features,
        pfc_explore=_pfc_explore_signal(bg_pfc_decision),
    )
    swarm_trace = _swarm_result_to_trace(swarm_result)
    answer, answer_source = _answer_from_swarm(swarm_result)
    await logger.log_event(
        trace_id=trace_id,
        module_name="execution.swarm",
        event_type="swarm.executed",
        payload={
            "status": swarm_trace.status,
            "elapsed_ms": swarm_trace.elapsed_ms,
            "plan_intent": swarm_trace.plan_intent,
            "answer_source": answer_source,
        },
    )

    return await _complete(
        logger, trace_id,
        path_taken=f"routed_{decision.path}",
        answer=answer,
        response_source="swarm",
        answer_source=answer_source,
        llm_mode=_state_llm_mode(state),
        route_decision=decision,
        difficulty=int(task_context.difficulty),
        category=forced_category,
        selected_tier=tier_name,
        epinephrine_active=eph_active,
        epinephrine_reason=task_context.epinephrine_reason,
        swarm_trace=swarm_trace,
    )


async def _run_pfc_decision(state, task_context, evaluation, session_id: str):
    """B10 — run PFC synchronously (LLM-free) WITH goal context, so BG/CR get a
    real pfc confidence/cue at routes time.

    This is a separate run from the swarm's PFC (the dual run is accepted — no
    swarm restructure). Unlike the swarm (which calls PFC with goal=None), routes
    builds the goal-stack summary from the session goal store, so the cue/confidence
    is higher-fidelity. Fail-open: returns None on any error (BG/CR then degrade to
    the no-signal path — never a fabricated value).
    """
    pfc = getattr(state, "pfc", None)
    if pfc is None or evaluation is None:
        return None
    try:
        goal_stack_summary = None
        active_goal = None
        goal_store = getattr(state, "session_goal_store", None)
        if goal_store is not None and session_id:
            context = await goal_store.get_or_create_session(session_id)
            goal_stack_summary = make_goal_stack_summary(context)
            active_goal = goal_stack_summary.top_goal
        return await pfc.infer_hint(
            query=task_context.prompt or "",
            eval_result=evaluation,
            goal_stack_summary=goal_stack_summary,
            active_goal=active_goal,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return None


# CR explore fires when the PFC is uncertain. C4 widened this from "a fallback cue
# (no goal/intent match) at conf<0.5" to "ANY cue whose real confidence is below
# this threshold" — so a borderline goal match (active/embedding match scored just
# over the 0.5 match threshold) also explores, not only no-match fallbacks. Uses
# PFC's real confidence float — no fabricated signal. Start value; tune after
# measurement.
_EXPLORE_CONF_THRESHOLD = 0.6


def _pfc_explore_signal(pfc_decision) -> bool:
    """CR explore mode = an *uncertain* PFC, measured by real confidence below
    _EXPLORE_CONF_THRESHOLD (any cue type). None decision → no explore."""
    if pfc_decision is None:
        return False
    return pfc_decision.hint.confidence < _EXPLORE_CONF_THRESHOLD


async def _basal_ganglia_apply(
    state, task_context, decision: RouteDecision, pfc_decision, *,
    trace_id: str, session_id: str,
) -> RouteDecision:
    """C2 BG apply (PROMOTE-ONLY) + B7/B10 observe pass.

    Runs at 8.2 — AFTER the routing decision is finalized
    (skip_router→override→ratchet) and route_path is stamped, but BEFORE the swarm
    and CR. Always runs the advisory evaluate (bg.evaluated telemetry; the
    ActionSelectionDecision.applied model rail stays False). When bg_apply_enabled,
    the recommendation is consumed PROMOTE-ONLY:

      - It may RAISE task_context.route_path (and the RouteDecision) to a heavier
        band — the redesigned BG escalates hard-in-disguise queries — but NEVER
        lowers it. So the ratchet no-demote floor and the B12 high-difficulty
        baseline (both already stamped upstream) can't be bypassed; the worst case
        is wasted compute, never a quality demotion.
      - BG runs after the ratchet, so a promotion is per-request (ephemeral) and
        does NOT raise the learned session floor (clean layer separation: BG never
        writes the 35-cell store).

    Returns the (possibly promoted) RouteDecision so the caller threads ONE
    consistent decision into CR (no desync). epinephrine is re-derived on promote.

    B10 fills the three signals with REAL values (no fabrication): PFC decision,
    ne_boost→{0.0, 1.0} (NE has no float), rpe_recent_counter sign counts. Unfilled
    degrades to None/0 — never a fake value.

    Fully fail-open (CancelledError re-raised); any failure returns the current
    decision (promotion, if already applied, stands).
    """
    advisor = getattr(state, "basal_ganglia", None)
    if advisor is None:
        return decision
    try:
        pfc_snapshot = None
        if pfc_decision is not None:
            hint = pfc_decision.hint
            pfc_snapshot = SimpleNamespace(
                pfc_active=True,
                cue_type=hint.cue_type,
                confidence=hint.confidence,
                intent_category=getattr(
                    pfc_decision.matched_goal, "category", None
                ),
            )
        # NE has no float — surface the real bool as {0.0, 1.0} (faithful, not an
        # invented continuous value). BG folds ne_level into its compute-demand D
        # (higher NE → higher demand → heavier candidate).
        lc_snapshot = SimpleNamespace(
            ne_level=1.0 if task_context.ne_boost else 0.0,
            intent_label=None,
        )
        positive = negative = 0
        counter = getattr(state, "rpe_recent_counter", None)
        if counter is not None and task_context.category:
            positive, negative = counter.counts(session_id, task_context.category)
        bg_ctx = build_action_selection_context_from_snapshots(
            trace_id=trace_id,
            session_id=session_id,
            category=task_context.category,
            difficulty=int(task_context.difficulty),
            pfc_snapshot=pfc_snapshot,
            lc_snapshot=lc_snapshot,
            synapse_weights=task_context.synapse_snapshot,
            rpe_recent_positive_count=positive,
            rpe_recent_negative_count=negative,
        )
        bg_decision = await advisor.evaluate(bg_ctx)  # bg.evaluated 로깅(applied=False)

        # ── C2 promote-only apply. Safe-off when state.settings is absent (tests /
        # degraded) — the production Settings default is True.
        apply_enabled = getattr(
            getattr(state, "settings", None), "bg_apply_enabled", False
        )
        if not apply_enabled or bg_decision.selected is None:
            return decision
        bg_band = route_path_for_candidate_type(bg_decision.selected.candidate_type)
        if bg_band is None or _band_index(bg_band) <= _band_index(decision.path):
            return decision  # promote-only: same/lower band (incl. demotion) ignored

        from_path = decision.path
        decision = decision.model_copy(
            update={
                "path": bg_band,
                "reason": (
                    f"{decision.reason} | bg_apply=promote {from_path}->{bg_band}"
                ),
            }
        )
        task_context.route_path = decision.path
        # Re-derive epinephrine (limit-break) — same rule as 8.1, now on the
        # promoted path (B11 S3b-promote).
        _is_limit_break = decision.path == "full_pipeline"
        task_context.epinephrine_active = _is_limit_break
        task_context.epinephrine_reason = "limit_break" if _is_limit_break else None
        await get_spinal_logger().log_event(
            trace_id=trace_id,
            module_name="basal_ganglia",
            event_type="bg.applied",
            payload={
                "from_path": from_path,
                "to_path": decision.path,
                "selected_type": bg_decision.selected.candidate_type,
                "promote_only": True,
                "category": task_context.category,
            },
        )
        return decision
    except asyncio.CancelledError:
        raise
    except Exception:
        return decision


# live LLM answer path 고정 문구. user-facing answer에는 provider/예외/키 문자열을
# 절대 포함하지 않는다 (generator_result.text / fallback_candidate / str(exc) 미surface).
_ANSWER_UNAVAILABLE = "[ANSWER UNAVAILABLE] generation unavailable"


def _answer_from_swarm(swarm_result) -> tuple[str, str]:
    """SwarmResult → (answer, answer_source).

    - generator 성공(finish_reason != "error") → (generator_result.text, "generator")
    - generator 실패(finish_reason == "error")  → (_ANSWER_UNAVAILABLE, "unavailable")

    실패 시에는 generator의 fallback 텍스트/예외 디테일을 answer로 위장하지 않고
    고정 unavailable 문구로 차단한다 (key/provider 정보 누출 방지).
    """
    gen = swarm_result.generator_result
    if gen.finish_reason == "error":
        return _ANSWER_UNAVAILABLE, "unavailable"
    return gen.text, "generator"


def _state_llm_mode(state) -> str:
    """app.state.llm_mode 안전 접근 (텔레메트리 라벨용).

    프로덕션 app은 main.py가 항상 app.state.llm_mode를 set한다(factory.get_llm_mode 소스).
    routes는 mock/live를 결정하지 않고(분리는 factory 책임) state 핸들만 읽는다 —
    factory 모듈을 import하지 않는다(STEP 3.3b 격리 규약). 레거시/베어 테스트 state에
    llm_mode가 없으면 기본 'mock'으로 본다(기본 모드가 mock이므로 안전).
    """
    return getattr(state, "llm_mode", None) or "mock"


def _swarm_result_to_trace(swarm_result) -> SwarmTrace:
    """SwarmResult → QueryResponse.swarm_trace 변환.

    overall status 우선순위:
      - 하나라도 "error"   → "error"
      - 하나라도 "timeout" → "timeout"
      - 하나라도 "fallback" → "degraded"
      - 모두 "ok"          → "ok"
    """
    statuses = [
        swarm_result.context_status,
        swarm_result.planner_status,
        swarm_result.generator_status,
    ]
    if "error" in statuses:
        overall = "error"
    elif "timeout" in statuses:
        overall = "timeout"
    elif "fallback" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    return SwarmTrace(
        executed=True,
        status=overall,
        elapsed_ms=swarm_result.total_elapsed_ms,
        context_status=swarm_result.context_status,
        planner_status=swarm_result.planner_status,
        generator_status=swarm_result.generator_status,
        generator_finish_reason=swarm_result.generator_result.finish_reason,
        generator_model_name=swarm_result.generator_result.model_name,
        plan_intent=swarm_result.final_plan.intent,
    )


async def _complete(
    logger,
    trace_id: str,
    *,
    path_taken: str,
    answer: str,
    response_source: str | None = None,
    answer_source: str | None = None,
    llm_mode: str | None = None,
    route_decision=None,
    difficulty: int | None = None,
    category: str | None = None,
    selected_tier: str | None = None,
    epinephrine_active: bool = False,
    epinephrine_reason: str | None = None,
    swarm_trace: SwarmTrace | None = None,
    glycine_active: bool = False,
    glycine_reason: str | None = None,
    glycine_action: str | None = None,
) -> QueryResponse:
    """Emit the closing trace event and assemble the response.

    selected_tier is intentionally Optional[str]: early-exit paths
    (thalamus / exact_cache / semantic_cache / tier_1_5) pass None;
    LC-routed paths pass ModelTier.name (never the IntEnum).

    Phase 4 STEP 3.3a — response_source labels the originating path
    (thalamus / exact_cache / semantic_cache / tier_1_5 / swarm /
    fallback). swarm_trace is left None here; STEP 3.3b populates it
    on the LC-routed path once AsyncSwarm is wired up.
    """
    await logger.log_event(
        trace_id=trace_id,
        module_name="api.routes",
        event_type="query.completed",
        payload={"path_taken": path_taken, "response_source": response_source},
    )
    return QueryResponse(
        trace_id=trace_id,
        answer=answer,
        path_taken=path_taken,
        route_decision=route_decision,
        difficulty=difficulty,
        category=category,
        selected_tier=selected_tier,
        epinephrine_active=epinephrine_active,
        epinephrine_reason=epinephrine_reason,
        response_source=response_source,
        answer_source=answer_source,
        llm_mode=llm_mode,
        swarm_trace=swarm_trace,
        glycine_active=glycine_active,
        glycine_reason=glycine_reason,
        glycine_action=glycine_action,
    )


@router.get("/trace/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    logger = get_spinal_logger()
    events = logger.get_trace(trace_id)
    return {"trace_id": trace_id, "events": [e.model_dump(mode="json") for e in events]}

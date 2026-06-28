"""demo_backend FastAPI 앱 (D1, 분리 배포 / 순수 JSON API).

CORTEX core(8000)를 httpx 프록시로 호출한다(직접 import 없음). HTML/StaticFiles 서빙 없음 —
프론트는 별도 배포되어 cross-origin 으로 호출하므로 CORS 화이트리스트만 연다.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from demo_backend import __version__
from demo_backend.cortex_client import (
    CortexClient,
    CortexResponseError,
    CortexUnreachableError,
)
from demo_backend.models import (
    AnswerView,
    CortexQueryView,
    DemoChatRequest,
    DemoChatResponse,
    GlycineView,
    NormalizedRunResult,
    ReadinessResponse,
    RouteDecisionView,
    SafetyInvariants,
    SwarmTraceView,
    TraceEnrichment,
)
from demo_backend.rate_limit import InMemoryRateLimiter
from demo_backend.settings import get_demo_settings

_SERVICE = "cortex-aev-demo-backend"
_RUN_STORE_CAP = 256

_READINESS_WARNINGS = [
    "Live LLM answers surface only when CORTEX runs in live mode (llm_mode=live).",
    "Mock-mode answers are never shown in the public demo UI (telemetry only).",
    "Planner cards are derived from route_decision and swarm_trace.",
]

# trace enrichment 방어 redaction — payload 에 만에 하나 들어올 민감 키 차단.
_TRACE_REDACT_KEYS = {
    "prompt", "raw_prompt", "raw", "api_key", "apikey",
    "authorization", "key", "token", "secret",
}


def _redact_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not events:
        return events
    cleaned: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        payload = ev.get("payload")
        if isinstance(payload, dict):
            payload = {
                k: v for k, v in payload.items() if k.lower() not in _TRACE_REDACT_KEYS
            }
            ev = {**ev, "payload": payload}
        cleaned.append(ev)
    return cleaned


def _build_answer_view(query_data: dict[str, Any], *, require_live: bool) -> AnswerView:
    """CORTEX /query 응답 → AnswerView (정직 라벨링 + public live-only 정책).

    하드 규칙: MockLLMClient answer 텍스트는 public demo에 절대 노출하지 않는다.
    live answer(=swarm & llm_mode=live & answer_source=generator)만 text를 표시한다.

    early-exit는 "stub"로 뭉치지 않고 성질별로 라벨링한다:
      thalamus→reflex, exact/semantic cache→cache, tier_1_5→tier_1_5_stub,
      glycine fallback→safety_blocked. (이들은 결정론적 시스템 응답 — text 표시.)

    require_live=True 면 live answer가 아닌 모든 run의 answer를 "Live mode unavailable"로
    차단한다(telemetry/trace는 영향 없음).
    """
    response_source = query_data.get("response_source")
    answer_source = query_data.get("answer_source")
    llm_mode = query_data.get("llm_mode")
    text = query_data.get("answer") or ""

    is_live_answer = (
        response_source == "swarm"
        and llm_mode == "live"
        and answer_source == "generator"
    )
    if is_live_answer:
        return AnswerView(text=text, mode="live", gated=False, source="live_generator")

    # 여기부터는 live LLM answer가 아니다.
    if require_live:
        # public live-only gate: 비-live answer 텍스트는 노출하지 않는다.
        return AnswerView(
            text="Live mode unavailable", mode="stub", gated=True, source="live_required",
        )

    if response_source == "swarm":
        if answer_source == "unavailable":
            return AnswerView(text="", mode="stub", gated=True, source="unavailable")
        # mock generator — 텍스트 숨김(하드 규칙).
        return AnswerView(text="", mode="stub", gated=True, source="mock_hidden")

    # 비-swarm 결정론적/시스템 응답 — 성질별 라벨 + 텍스트 표시.
    source_map: dict[str, tuple[str, bool]] = {
        "thalamus": ("reflex", False),
        "exact_cache": ("cache", False),
        "semantic_cache": ("cache", False),
        "tier_1_5": ("tier_1_5_stub", True),
        "fallback": ("safety_blocked", True),
    }
    src, gated = source_map.get(response_source or "", (None, True))
    return AnswerView(text=text, mode="stub", gated=gated, source=src)


def _normalize(
    query_data: dict[str, Any],
    *,
    run_id: str,
    session_id: str,
    trace_data: dict[str, Any] | None,
    require_live: bool = False,
) -> NormalizedRunResult:
    rd = query_data.get("route_decision") or None
    route_view = (
        RouteDecisionView(
            path=rd.get("path"),
            skip_layers=rd.get("skip_layers") or [],
            reason=rd.get("reason"),
        )
        if isinstance(rd, dict)
        else None
    )

    st = query_data.get("swarm_trace") or None
    swarm_view = SwarmTraceView(**{
        k: st.get(k) for k in SwarmTraceView.model_fields
    }) if isinstance(st, dict) else None

    cortex = CortexQueryView(
        trace_id=query_data.get("trace_id"),
        path_taken=query_data.get("path_taken"),
        category=query_data.get("category"),
        difficulty=query_data.get("difficulty"),
        route_decision=route_view,
        selected_tier=query_data.get("selected_tier"),
        epinephrine_active=bool(query_data.get("epinephrine_active", False)),
        epinephrine_reason=query_data.get("epinephrine_reason"),
        response_source=query_data.get("response_source"),
        swarm_trace=swarm_view,
        glycine=GlycineView(
            active=bool(query_data.get("glycine_active", False)),
            reason=query_data.get("glycine_reason"),
            action=query_data.get("glycine_action"),
        ),
        answer=_build_answer_view(query_data, require_live=require_live),
    )

    events = _redact_events(trace_data.get("events")) if isinstance(trace_data, dict) else None
    trace_enrich = TraceEnrichment(
        available=bool(events),
        event_count=len(events) if events else 0,
        events=events,
    )

    return NormalizedRunResult(
        run_id=run_id,
        session_id=session_id,
        status="done",
        created_at=datetime.now(timezone.utc).isoformat(),
        cortex=cortex,
        # per-run 정직 반영: 이 run이 실제 live LLM이었는지.
        safety_invariants=SafetyInvariants(
            llm_live_enabled=(query_data.get("llm_mode") == "live"),
        ),
        trace=trace_enrich,
    )


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self._max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large."},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_demo_settings()
    app.state.settings = settings
    app.state.cortex = CortexClient(
        settings.cortex_url,
        query_timeout=settings.cortex_query_timeout,
        health_timeout=settings.cortex_health_timeout,
        trace_timeout=settings.cortex_trace_timeout,
    )
    app.state.rate_limiter = InMemoryRateLimiter(
        per_minute=settings.rate_per_minute,
        per_session=settings.rate_per_session,
        global_total=settings.rate_global,
    )
    app.state.run_store = OrderedDict()
    try:
        yield
    finally:
        await app.state.cortex.aclose()


def create_demo_app() -> FastAPI:
    settings = get_demo_settings()
    app = FastAPI(
        title="CORTEX-AEV Demo Backend",
        version=__version__,
        description="JSON API proxy in front of CORTEX-AEV core (demo, D1).",
        lifespan=lifespan,
    )

    # cross-origin 화이트리스트 — 와일드카드 금지.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    @app.get("/")
    async def root() -> dict:
        return {
            "service": _SERVICE,
            "version": __version__,
            "endpoints": [
                "/demo/health",
                "/demo/readiness",
                "/demo/chat",
                "/demo/runs/{run_id}",
            ],
        }

    @app.get("/demo/health")
    async def demo_health() -> dict:
        return {
            "status": "ok",
            "service": _SERVICE,
            "version": __version__,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/demo/readiness", response_model=ReadinessResponse)
    async def demo_readiness(request: Request) -> ReadinessResponse:
        settings = request.app.state.settings
        # core /health 를 중계만 한다 — demo 는 키/벤더/모드를 자체 판단하지 않는다.
        # core 도달 실패(httpx 에러/타임아웃/비200)는 health()가 None 으로 흡수 →
        # graceful not-ready (거짓 ready 금지, 500 금지). health 가 None 일 때
        # .get 을 부르지 않도록 가드한다.
        health = await request.app.state.cortex.health()
        cortex_reachable = health is not None
        llm_mode = health.get("llm_mode") if health else None
        slots_ready = bool(health.get("slots_ready")) if health else False
        # live-ready = core 가 live 모드 AND 5칸 슬롯이 전부 키 구비(strict).
        llm_live_enabled = llm_mode == "live"
        can_run_live_llm = llm_live_enabled and slots_ready
        return ReadinessResponse(
            cortex_reachable=cortex_reachable,
            cortex_url=settings.cortex_url,
            demo_mode="live" if can_run_live_llm else "stub",
            slots_ready=slots_ready,
            llm_live_enabled=llm_live_enabled,
            can_run_query=cortex_reachable,
            can_run_live_llm=can_run_live_llm,
            active_learning_enabled=False,
            basal_ganglia_applied=False,
            conflict_resolution="deferred",
            warnings=list(_READINESS_WARNINGS),
        )

    @app.post("/demo/chat", response_model=DemoChatResponse)
    async def demo_chat(payload: DemoChatRequest, request: Request) -> DemoChatResponse:
        state = request.app.state

        rl = state.rate_limiter.check(payload.session_id)
        if not rl.allowed:
            headers = {"Retry-After": str(rl.retry_after)} if rl.retry_after else None
            raise HTTPException(
                status_code=429,
                detail={"error": "rate_limited", "scope": rl.scope, "limit": rl.limit},
                headers=headers,
            )

        try:
            query_data = await state.cortex.query(payload.message, payload.session_id)
        except CortexUnreachableError:
            raise HTTPException(status_code=503, detail="CORTEX core is unreachable.") from None
        except CortexResponseError as exc:
            raise HTTPException(
                status_code=502, detail=f"CORTEX core error (HTTP {exc.status_code})."
            ) from None

        trace_id = query_data.get("trace_id")
        trace_data = await state.cortex.get_trace(trace_id) if trace_id else None

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        result = _normalize(
            query_data,
            run_id=run_id,
            session_id=payload.session_id,
            trace_data=trace_data,
            require_live=state.settings.require_live,
        )

        store: OrderedDict = state.run_store
        store[run_id] = result
        while len(store) > _RUN_STORE_CAP:
            store.popitem(last=False)

        return DemoChatResponse(
            run_id=run_id, status="done", result_url=f"/demo/runs/{run_id}"
        )

    @app.get("/demo/runs/{run_id}", response_model=NormalizedRunResult)
    async def demo_run(run_id: str, request: Request) -> NormalizedRunResult:
        result = request.app.state.run_store.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run_id not found.")
        return result

    return app


app = create_demo_app()

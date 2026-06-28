"""Execution Layer 팩토리 — LLMClient 분기 + Swarm 의존성 조립."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.execution.category_selector import CategorySelector
from app.execution.chromadb_searcher import ChromaDBSearcher
from app.execution.context_agent import ContextAgent
from app.execution.gaba import GABAFilter
from app.execution.generator_agent import GeneratorAgent
from app.execution.live_llm_client import LiveLLMClient
from app.execution.llm_client import LLMClientProtocol
from app.execution.mock_llm_client import MockLLMClient
from app.execution.planner_agent import PlannerAgent
from app.execution.swarm import AsyncSwarm
from app.routing.neuromodulators import Norepinephrine

if TYPE_CHECKING:
    from app.maintenance.plc import PLC
    from app.routing.pfc import PFCIntegrationConfig, PrefrontalCortex


def get_llm_mode() -> str:
    """CORTEX_LLM_MODE 환경변수를 정규화해 반환한다 (기본 'mock').

    get_llm_client과 동일한 단일 소스(CORTEX_LLM_MODE)를 읽는다. 알 수 없는 값은
    여기서 검증하지 않고 그대로 소문자화해 반환한다(검증은 get_llm_client 책임).
    routes/main이 응답의 llm_mode 텔레메트리를 채울 때 사용한다.
    """
    return os.getenv("CORTEX_LLM_MODE", "mock").lower()


def get_llm_client() -> LLMClientProtocol:
    """환경 변수 CORTEX_LLM_MODE에 따라 적절한 클라이언트 반환.

    기본값: mock. 값: "mock" / "live".

    live 모드는 슬롯 기반 LiveLLMClient(Tier Slot Registry, V4)를 사용한다.
    슬롯 설정/키는 호출 시 동적 조회되며, 설정 파일이 없으면 NO-GO로 차단된다.
    mock 모드(기본)는 MockLLMClient로 슬롯/키 없이 결정론적으로 동작한다.
    """
    mode = get_llm_mode()

    if mode == "live":
        return LiveLLMClient()
    if mode == "mock":
        return MockLLMClient()
    raise ValueError(
        f"Unknown CORTEX_LLM_MODE: {mode}. Expected 'mock' or 'live'."
    )


def build_execution_swarm(
    *,
    chroma_collection,
    embedder,
    llm_client: LLMClientProtocol,
    norepinephrine: Norepinephrine,
    context_timeout: float = 5.0,
    plc: "PLC | None" = None,
    pfc: "PrefrontalCortex | None" = None,
    pfc_config: "PFCIntegrationConfig | None" = None,
) -> AsyncSwarm:
    """Execution Layer 의존성 조립 (Phase 4 STEP 3.3b / STEP 4).

    main.py lifespan에서 호출되어 app.state.async_swarm으로 부착된다.

    Dependency graph::

        AsyncSwarm
            ├── ContextAgent
            │       ├── CategorySelector  (threshold 0.4)
            │       ├── ChromaDBSearcher   (collection + embedder)
            │       └── GABAFilter         (threshold 0.5)
            ├── PlannerAgent               (Phase 5 STEP 4: PFC config 주입)
            ├── GeneratorAgent             (llm_client + norepinephrine)
            ├── PLC (optional)             (Phase 4 STEP 4 field-level lock)
            └── PrefrontalCortex (optional) (Phase 5 STEP 4 bounded hint)

    이 함수는 execution 내부 조립 전용 — routes.py / API schema /
    main.py를 import 하지 않는다 (core dependency rule).
    """
    context_agent = ContextAgent(
        selector=CategorySelector(),
        searcher=ChromaDBSearcher(collection=chroma_collection, embedder=embedder),
        gaba=GABAFilter(),
    )
    planner_agent = PlannerAgent(pfc_config=pfc_config)
    generator_agent = GeneratorAgent(
        llm_client=llm_client,
        norepinephrine=norepinephrine,
    )
    return AsyncSwarm(
        context_agent=context_agent,
        planner_agent=planner_agent,
        generator_agent=generator_agent,
        context_timeout=context_timeout,
        plc=plc,
        pfc=pfc,
        pfc_config=pfc_config,
    )

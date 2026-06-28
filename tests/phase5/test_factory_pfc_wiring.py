"""Phase 5 STEP 4 — factory.py PFC 의존성 주입 테스트."""
from __future__ import annotations

import pytest

from app.execution.factory import build_execution_swarm
from app.execution.swarm import AsyncSwarm
from app.routing.pfc import PFCIntegrationConfig, PrefrontalCortex
from app.routing.neuromodulators import Norepinephrine


# ---------------------------------------------------------------------------
# 헬퍼 — 공통 인자 생성
# ---------------------------------------------------------------------------


def _chroma_collection():
    """chromadb collection stub (duck-typing 충분)."""

    class StubCollection:
        pass

    return StubCollection()


def _embedder():
    """embedder stub."""

    def embedder(texts):
        return [[0.0] * 10 for _ in texts]

    return embedder


from app.execution.mock_llm_client import MockLLMClient


def _base_kwargs():
    return {
        "chroma_collection": _chroma_collection(),
        "embedder": _embedder(),
        "llm_client": MockLLMClient(),
        "norepinephrine": Norepinephrine(),
    }


# ---------------------------------------------------------------------------
# pfc=None (기본) — AsyncSwarm._pfc is None
# ---------------------------------------------------------------------------


def test_factory_pfc_none_by_default():
    swarm = build_execution_swarm(**_base_kwargs())
    assert isinstance(swarm, AsyncSwarm)
    assert swarm._pfc is None


def test_factory_pfc_config_none_by_default():
    swarm = build_execution_swarm(**_base_kwargs())
    assert swarm._pfc_config is None


# ---------------------------------------------------------------------------
# pfc 주입 — AsyncSwarm._pfc is 설정된 PrefrontalCortex
# ---------------------------------------------------------------------------


def test_factory_pfc_injected():
    pfc = PrefrontalCortex()
    swarm = build_execution_swarm(**_base_kwargs(), pfc=pfc)
    assert swarm._pfc is pfc


def test_factory_pfc_config_injected():
    pfc = PrefrontalCortex()
    cfg = PFCIntegrationConfig(hint_timeout_ms=20.0)
    swarm = build_execution_swarm(**_base_kwargs(), pfc=pfc, pfc_config=cfg)
    assert swarm._pfc_config is cfg
    assert swarm._pfc_config.hint_timeout_ms == 20.0


# ---------------------------------------------------------------------------
# PlannerAgent에 pfc_config 전달 확인
# ---------------------------------------------------------------------------


def test_factory_planner_receives_pfc_config():
    """build_execution_swarm이 PlannerAgent에 pfc_config를 전달함."""
    pfc = PrefrontalCortex()
    cfg = PFCIntegrationConfig(pfc_confidence_threshold=0.85)
    swarm = build_execution_swarm(**_base_kwargs(), pfc=pfc, pfc_config=cfg)
    assert swarm._planner_agent._pfc_config.pfc_confidence_threshold == 0.85


def test_factory_planner_default_config_when_no_pfc():
    """pfc=None 시 PlannerAgent는 default PFCIntegrationConfig를 사용."""
    swarm = build_execution_swarm(**_base_kwargs())
    assert swarm._planner_agent._pfc_config.pfc_confidence_threshold == 0.7


# ---------------------------------------------------------------------------
# pfc 주입 시 pfc_config=None → default config 자동 생성
# ---------------------------------------------------------------------------


def test_factory_pfc_auto_config_when_pfc_injected_without_config():
    """pfc 주입 + pfc_config=None → AsyncSwarm 내부에서 default config 생성."""
    pfc = PrefrontalCortex()
    swarm = build_execution_swarm(**_base_kwargs(), pfc=pfc, pfc_config=None)
    assert swarm._pfc_config is not None
    assert swarm._pfc_config.hint_timeout_ms == 30.0


# ---------------------------------------------------------------------------
# 반환 타입
# ---------------------------------------------------------------------------


def test_factory_returns_async_swarm():
    swarm = build_execution_swarm(**_base_kwargs())
    assert isinstance(swarm, AsyncSwarm)


def test_factory_with_pfc_returns_async_swarm():
    pfc = PrefrontalCortex()
    swarm = build_execution_swarm(**_base_kwargs(), pfc=pfc)
    assert isinstance(swarm, AsyncSwarm)

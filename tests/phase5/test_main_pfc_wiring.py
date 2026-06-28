"""Phase 5 STEP 4 — app.state PFC 와이어링 검증.

create_app() 실행 후 PFC 관련 state가 올바르게 부착되어 있는지 확인한다.
"""
from __future__ import annotations

import pytest

from app.execution.swarm import AsyncSwarm
from app.main import app
from app.routing.pfc import PrefrontalCortex


# ---------------------------------------------------------------------------
# app.state.pfc 존재 + 타입
# ---------------------------------------------------------------------------


def test_app_state_has_pfc():
    assert hasattr(app.state, "pfc")


def test_app_state_pfc_is_prefrontal_cortex():
    assert isinstance(app.state.pfc, PrefrontalCortex)


# ---------------------------------------------------------------------------
# AsyncSwarm에 PFC가 주입되어 있는지
# ---------------------------------------------------------------------------


def test_async_swarm_has_pfc():
    swarm: AsyncSwarm = app.state.async_swarm
    assert swarm._pfc is not None


def test_async_swarm_pfc_is_same_object_as_app_state_pfc():
    """AsyncSwarm._pfc는 app.state.pfc와 동일한 객체여야 한다."""
    swarm: AsyncSwarm = app.state.async_swarm
    assert swarm._pfc is app.state.pfc


# ---------------------------------------------------------------------------
# pfc_config 존재 + 기본값
# ---------------------------------------------------------------------------


def test_async_swarm_pfc_config_not_none():
    swarm: AsyncSwarm = app.state.async_swarm
    assert swarm._pfc_config is not None


def test_async_swarm_pfc_config_default_timeout():
    from app.routing.pfc import PFCIntegrationConfig
    swarm: AsyncSwarm = app.state.async_swarm
    assert isinstance(swarm._pfc_config, PFCIntegrationConfig)
    assert swarm._pfc_config.hint_timeout_ms == 30.0


# ---------------------------------------------------------------------------
# PlannerAgent에 pfc_config 전달 확인
# ---------------------------------------------------------------------------


def test_planner_agent_has_pfc_config():
    swarm: AsyncSwarm = app.state.async_swarm
    assert swarm._planner_agent._pfc_config is not None


def test_planner_agent_pfc_config_threshold():
    swarm: AsyncSwarm = app.state.async_swarm
    assert swarm._planner_agent._pfc_config.pfc_confidence_threshold == 0.7

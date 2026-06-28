"""Phase 5 STEP 4 — AsyncSwarm PFC logging 테스트.

SpinalLogger에 올바른 event_type이 기록되는지 확인한다.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.schemas.context import TaskContext
from app.execution.swarm import AsyncSwarm
from app.routing.pfc import PFCDecision, PFCHint, PFCIntegrationConfig
from tests.phase4._swarm_mocks import MockContextAgent, MockGeneratorAgent
from tests.phase5.test_swarm_pfc_integration import (
    MockPFCFast,
    MockPFCRaises,
    MockPFCSlow,
    PFCAwareMockPlanner,
    _decision,
)


# ---------------------------------------------------------------------------
# SpinalLogger 캡처용 mock
# ---------------------------------------------------------------------------


class CapturingLogger:
    """log_event 호출을 캡처하는 logger mock."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def new_trace(self) -> str:
        return "mock-trace-log"

    async def log_event(
        self,
        *,
        trace_id: str,
        module_name: str,
        event_type: str,
        payload: dict,
    ) -> None:
        self.events.append(
            {
                "trace_id": trace_id,
                "module_name": module_name,
                "event_type": event_type,
                "payload": payload,
            }
        )

    def event_types(self) -> list[str]:
        return [e["event_type"] for e in self.events]

    def find(self, event_type: str) -> dict | None:
        return next((e for e in self.events if e["event_type"] == event_type), None)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _task_ctx() -> TaskContext:
    return TaskContext(
        trace_id="trace-log-001",
        prompt="logging test",
        category="coding",
        difficulty=1,
    )


def _swarm_with_logger(pfc, capturing_logger, pfc_config=None) -> AsyncSwarm:
    """SpinalLogger 싱글턴을 patch한 swarm 생성."""
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
        pfc_config=pfc_config,
    )
    return swarm, capturing_logger


# ---------------------------------------------------------------------------
# pfc.completed 이벤트 (PFC 성공)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_completed_event_logged(monkeypatch):
    """PFC 정상 완료 시 pfc.completed 이벤트가 logging됨."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCFast(_decision("continuation", confidence=0.9))
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    await swarm.execute(_task_ctx())
    assert "pfc.completed" in capturing.event_types()


@pytest.mark.asyncio
async def test_pfc_completed_payload_has_cue_type(monkeypatch):
    """pfc.completed payload에 cue_type이 포함됨."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCFast(_decision("continuation", confidence=0.9))
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    await swarm.execute(_task_ctx())
    event = capturing.find("pfc.completed")
    assert event is not None
    assert event["payload"]["cue_type"] == "continuation"
    assert event["payload"]["confidence"] == 0.9


# ---------------------------------------------------------------------------
# pfc.timeout 이벤트
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_timeout_event_logged(monkeypatch):
    """PFC timeout 시 pfc.timeout 이벤트가 logging됨."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCSlow(_decision(), delay=1.0)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=10.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
        pfc_config=pfc_config,
    )
    await swarm.execute(_task_ctx())
    assert "pfc.timeout" in capturing.event_types()


@pytest.mark.asyncio
async def test_pfc_timeout_payload_has_timeout_ms(monkeypatch):
    """pfc.timeout payload에 timeout_ms가 포함됨."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCSlow(_decision(), delay=1.0)
    pfc_config = PFCIntegrationConfig(hint_timeout_ms=10.0)
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
        pfc_config=pfc_config,
    )
    await swarm.execute(_task_ctx())
    event = capturing.find("pfc.timeout")
    assert event is not None
    assert event["payload"]["timeout_ms"] == 10.0


# ---------------------------------------------------------------------------
# pfc.error 이벤트
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_error_event_logged(monkeypatch):
    """PFC 예외 발생 시 pfc.error 이벤트가 logging됨."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCRaises(RuntimeError("test error"))
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    await swarm.execute(_task_ctx())
    assert "pfc.error" in capturing.event_types()


@pytest.mark.asyncio
async def test_pfc_error_payload_has_error_type(monkeypatch):
    """pfc.error payload에 error_type이 포함됨."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCRaises(ValueError("bad value"))
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    await swarm.execute(_task_ctx())
    event = capturing.find("pfc.error")
    assert event is not None
    assert event["payload"]["error_type"] == "ValueError"


# ---------------------------------------------------------------------------
# module_name 검증
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_events_have_correct_module_name(monkeypatch):
    """모든 PFC 이벤트의 module_name은 'execution.swarm'."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    pfc = MockPFCFast(_decision())
    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=pfc,
    )
    await swarm.execute(_task_ctx())
    pfc_events = [e for e in capturing.events if e["event_type"].startswith("pfc.")]
    assert all(e["module_name"] == "execution.swarm" for e in pfc_events)


# ---------------------------------------------------------------------------
# Phase 4 경로 (pfc=None) — PFC 이벤트 없음
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_path_no_pfc_events(monkeypatch):
    """pfc=None (Phase 4 경로)에서는 pfc.* 이벤트가 logging되지 않음."""
    capturing = CapturingLogger()
    monkeypatch.setattr("app.execution.swarm.get_spinal_logger", lambda: capturing)

    swarm = AsyncSwarm(
        context_agent=MockContextAgent(),
        planner_agent=PFCAwareMockPlanner(),
        generator_agent=MockGeneratorAgent(),
        pfc=None,
    )
    await swarm.execute(_task_ctx())
    pfc_events = [e for e in capturing.events if e["event_type"].startswith("pfc.")]
    assert len(pfc_events) == 0

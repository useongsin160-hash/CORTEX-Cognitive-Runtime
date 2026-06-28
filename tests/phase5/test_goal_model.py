"""Phase 5 STEP 1 — Goal 모델 단위 테스트."""
from __future__ import annotations

import json
import time

import pytest
from pydantic import ValidationError

from app.memory.goal import Goal, GoalSource, GoalStatus, make_goal


# ---------------------------------------------------------------------------
# Goal 생성 기본
# ---------------------------------------------------------------------------

def test_make_goal_returns_goal_instance():
    g = make_goal(title="테스트 목표", source="user_explicit")
    assert isinstance(g, Goal)


def test_make_goal_status_is_active_by_default():
    g = make_goal(title="새 목표", source="pfc_inferred")
    assert g.status == "active"


def test_make_goal_goal_id_format():
    g = make_goal(title="목표", source="system")
    assert g.goal_id.startswith("goal_")
    hex_part = g.goal_id[len("goal_"):]
    assert len(hex_part) == 12
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_make_goal_uses_monotonic_time():
    before = time.monotonic()
    g = make_goal(title="시간 테스트", source="user_explicit")
    after = time.monotonic()
    assert before <= g.created_at <= after
    assert before <= g.updated_at <= after
    assert before <= g.last_used_at <= after


def test_make_goal_created_updated_last_used_equal_at_creation():
    g = make_goal(title="시간 일관성", source="user_explicit")
    assert g.created_at == g.updated_at == g.last_used_at


# ---------------------------------------------------------------------------
# source별 기본 priority
# ---------------------------------------------------------------------------

def test_make_goal_user_explicit_default_priority():
    g = make_goal(title="명시적 목표", source="user_explicit")
    assert g.priority == 0.8


def test_make_goal_pfc_inferred_default_priority():
    g = make_goal(title="추론 목표", source="pfc_inferred")
    assert g.priority == 0.5


def test_make_goal_system_default_priority():
    g = make_goal(title="시스템 목표", source="system")
    assert g.priority == 0.3


def test_make_goal_explicit_priority_overrides_default():
    g = make_goal(title="커스텀 우선순위", source="user_explicit", priority=0.6)
    assert g.priority == 0.6


# ---------------------------------------------------------------------------
# priority 범위 검증
# ---------------------------------------------------------------------------

def test_goal_priority_zero_is_valid():
    g = make_goal(title="최저 우선순위", source="system", priority=0.0)
    assert g.priority == 0.0


def test_goal_priority_one_is_valid():
    g = make_goal(title="최고 우선순위", source="user_explicit", priority=1.0)
    assert g.priority == 1.0


def test_goal_priority_above_one_raises_validation_error():
    with pytest.raises(ValidationError):
        Goal(
            goal_id="goal_test000001",
            title="범위 초과",
            priority=1.01,
            created_at=time.monotonic(),
            updated_at=time.monotonic(),
            last_used_at=time.monotonic(),
        )


def test_goal_priority_below_zero_raises_validation_error():
    with pytest.raises(ValidationError):
        Goal(
            goal_id="goal_test000002",
            title="음수 우선순위",
            priority=-0.01,
            created_at=time.monotonic(),
            updated_at=time.monotonic(),
            last_used_at=time.monotonic(),
        )


# ---------------------------------------------------------------------------
# status 열거값 검증
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["active", "paused", "completed", "expired"])
def test_goal_all_valid_statuses(status: str):
    g = Goal(
        goal_id=f"goal_{status}000001",
        title=f"{status} 목표",
        status=status,  # type: ignore[arg-type]
        created_at=time.monotonic(),
        updated_at=time.monotonic(),
        last_used_at=time.monotonic(),
    )
    assert g.status == status


def test_goal_invalid_status_raises_validation_error():
    with pytest.raises(ValidationError):
        Goal(
            goal_id="goal_bad_status001",
            title="잘못된 상태",
            status="unknown",  # type: ignore[arg-type]
            created_at=time.monotonic(),
            updated_at=time.monotonic(),
            last_used_at=time.monotonic(),
        )


# ---------------------------------------------------------------------------
# source 열거값 검증
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["user_explicit", "pfc_inferred", "system"])
def test_goal_all_valid_sources(source: str):
    g = make_goal(title="소스 테스트", source=source)  # type: ignore[arg-type]
    assert g.source == source


def test_goal_invalid_source_raises_validation_error():
    with pytest.raises(ValidationError):
        Goal(
            goal_id="goal_bad_source001",
            title="잘못된 소스",
            source="unknown",  # type: ignore[arg-type]
            created_at=time.monotonic(),
            updated_at=time.monotonic(),
            last_used_at=time.monotonic(),
        )


# ---------------------------------------------------------------------------
# 선택적 필드
# ---------------------------------------------------------------------------

def test_make_goal_optional_fields_default_none():
    g = make_goal(title="기본값 테스트", source="system")
    assert g.category is None
    assert g.summary is None
    assert g.source_trace_id is None
    assert g.session_id is None


def test_make_goal_with_all_optional_fields():
    g = make_goal(
        title="전체 필드",
        source="user_explicit",
        session_id="sess_abc",
        source_trace_id="trace_xyz",
        category="coding",
        summary="코딩 관련 목표",
    )
    assert g.session_id == "sess_abc"
    assert g.source_trace_id == "trace_xyz"
    assert g.category == "coding"
    assert g.summary == "코딩 관련 목표"


# ---------------------------------------------------------------------------
# JSON 직렬화
# ---------------------------------------------------------------------------

def test_goal_json_serializable():
    g = make_goal(title="직렬화 테스트", source="pfc_inferred")
    data = json.loads(g.model_dump_json())
    assert data["goal_id"] == g.goal_id
    assert data["title"] == "직렬화 테스트"
    assert data["source"] == "pfc_inferred"
    assert data["priority"] == 0.5
    assert data["status"] == "active"


def test_goal_model_dump_contains_all_required_fields():
    g = make_goal(title="필드 검증", source="system")
    d = g.model_dump()
    required = {
        "goal_id", "title", "priority", "status", "source",
        "created_at", "updated_at", "last_used_at",
        "category", "summary", "source_trace_id", "session_id",
    }
    assert required.issubset(d.keys())

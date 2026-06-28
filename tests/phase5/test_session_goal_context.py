"""Phase 5 STEP 1 — SessionGoalContext 단위 테스트."""
from __future__ import annotations

import pytest

from app.memory.goal import make_goal
from app.memory.goal_stack import GoalStackConfig
from app.memory.session_goal_context import SessionGoalContext


def _make(title: str = "목표", priority: float = 0.5):
    return make_goal(title=title, source="user_explicit", priority=priority)


# ---------------------------------------------------------------------------
# for_session / for_trace classmethod
# ---------------------------------------------------------------------------

def test_for_session_scope_type():
    ctx = SessionGoalContext.for_session("sess_abc")
    assert ctx.scope_type == "session"
    assert ctx.scope_id == "sess_abc"


def test_for_trace_scope_type():
    ctx = SessionGoalContext.for_trace("trace_xyz")
    assert ctx.scope_type == "trace"
    assert ctx.scope_id == "trace_xyz"


def test_for_session_and_trace_are_different_instances():
    ctx_s = SessionGoalContext.for_session("same_id")
    ctx_t = SessionGoalContext.for_trace("same_id")
    assert ctx_s is not ctx_t
    assert ctx_s.scope_type != ctx_t.scope_type


def test_for_session_has_empty_stack():
    ctx = SessionGoalContext.for_session("sess_001")
    assert len(ctx.goal_stack) == 0


def test_for_trace_has_empty_stack():
    ctx = SessionGoalContext.for_trace("trace_001")
    assert len(ctx.goal_stack) == 0


def test_for_session_accepts_custom_config():
    cfg = GoalStackConfig(max_depth=3)
    ctx = SessionGoalContext.for_session("sess_cfg", cfg)
    assert ctx.goal_stack._config.max_depth == 3


# ---------------------------------------------------------------------------
# add_goal
# ---------------------------------------------------------------------------

def test_add_goal_increases_stack_size():
    ctx = SessionGoalContext.for_session("sess_add")
    g = _make()
    ctx.add_goal(g)
    assert len(ctx.goal_stack) == 1


def test_add_goal_returns_none_when_no_eviction():
    ctx = SessionGoalContext.for_session("sess_no_evict")
    g = _make()
    evicted = ctx.add_goal(g)
    assert evicted is None


def test_add_goal_returns_evicted_when_full():
    cfg = GoalStackConfig(max_depth=1)
    ctx = SessionGoalContext.for_session("sess_evict", cfg)
    g1 = _make("첫 번째")
    g2 = _make("두 번째")
    ctx.add_goal(g1)
    evicted = ctx.add_goal(g2)
    assert evicted is not None


def test_add_goal_updates_updated_at():
    import time
    ctx = SessionGoalContext.for_session("sess_ts")
    old_updated = ctx.updated_at
    g = _make()
    ctx.add_goal(g)
    assert ctx.updated_at >= old_updated


# ---------------------------------------------------------------------------
# set_active
# ---------------------------------------------------------------------------

def test_set_active_marks_goal_as_active():
    ctx = SessionGoalContext.for_session("sess_sa")
    g = _make()
    ctx.add_goal(g)
    ctx.set_active(g.goal_id)
    current = ctx.goal_stack.get(g.goal_id)
    assert current.status == "active"


def test_set_active_pauses_previous_active():
    ctx = SessionGoalContext.for_session("sess_sa2")
    g1 = _make("첫 번째")
    g2 = _make("두 번째")
    ctx.add_goal(g1)
    ctx.add_goal(g2)
    # g1을 먼저 active로 설정 후 g2로 전환
    ctx.set_active(g1.goal_id)
    ctx.set_active(g2.goal_id)
    assert ctx.goal_stack.get(g1.goal_id).status == "paused"
    assert ctx.goal_stack.get(g2.goal_id).status == "active"


def test_set_active_sets_last_active_goal_id():
    ctx = SessionGoalContext.for_session("sess_la")
    g = _make()
    ctx.add_goal(g)
    ctx.set_active(g.goal_id)
    assert ctx.last_active_goal_id == g.goal_id


def test_set_active_nonexistent_raises_key_error():
    ctx = SessionGoalContext.for_session("sess_ke")
    with pytest.raises(KeyError):
        ctx.set_active("nonexistent_goal_id")


def test_set_active_idempotent():
    """같은 goal에 set_active 여러 번 호출해도 안전."""
    ctx = SessionGoalContext.for_session("sess_idem")
    g = _make()
    ctx.add_goal(g)
    ctx.set_active(g.goal_id)
    ctx.set_active(g.goal_id)
    assert ctx.goal_stack.get(g.goal_id).status == "active"
    assert ctx.last_active_goal_id == g.goal_id


def test_set_active_only_one_active_at_a_time():
    ctx = SessionGoalContext.for_session("sess_one_active")
    goals = [_make(f"목표 {i}") for i in range(3)]
    for g in goals:
        ctx.add_goal(g)
    ctx.set_active(goals[0].goal_id)
    ctx.set_active(goals[1].goal_id)
    ctx.set_active(goals[2].goal_id)
    active_list = ctx.goal_stack.list_by_status("active")
    assert len(active_list) == 1
    assert active_list[0].goal_id == goals[2].goal_id


# ---------------------------------------------------------------------------
# get_active_goal
# ---------------------------------------------------------------------------

def test_get_active_goal_returns_last_active():
    ctx = SessionGoalContext.for_session("sess_ga")
    g = _make()
    ctx.add_goal(g)
    ctx.set_active(g.goal_id)
    result = ctx.get_active_goal()
    assert result is not None
    assert result.goal_id == g.goal_id


def test_get_active_goal_returns_none_when_empty():
    ctx = SessionGoalContext.for_session("sess_empty")
    assert ctx.get_active_goal() is None


def test_get_active_goal_stale_last_active_fallback():
    """last_active_goal_id가 가리키는 goal이 삭제된 경우 get_top_goal fallback.

    주의: set_active(g1)을 사용하면 g2가 paused로 바뀌어 fallback이 None이 됨.
    직접 last_active_goal_id를 설정해 stale 상황을 시뮬레이션한다.
    """
    ctx = SessionGoalContext.for_session("sess_stale")
    g1 = _make("삭제될 목표", priority=0.3)
    g2 = _make("남은 목표", priority=0.7)
    ctx.add_goal(g1)
    ctx.add_goal(g2)
    # set_active 없이 직접 last_active_goal_id 설정 (g2를 active 상태로 유지)
    ctx.last_active_goal_id = g1.goal_id
    # g1 강제 삭제
    ctx.goal_stack.remove(g1.goal_id)
    # last_active_goal_id는 g1이지만 stale (g1이 삭제됨)
    result = ctx.get_active_goal()
    # get_top_goal fallback → g2 (active 상태 그대로, higher priority)
    assert result is not None
    assert result.goal_id == g2.goal_id


def test_get_active_goal_stale_when_status_changed():
    """last_active_goal_id의 goal이 completed로 변경된 경우 fallback.

    주의: set_active(g1)을 사용하면 g2가 paused로 바뀌므로,
    직접 last_active_goal_id를 설정해 stale 상황을 시뮬레이션한다.
    """
    ctx = SessionGoalContext.for_session("sess_stale2")
    g1 = _make("완료된 목표", priority=0.5)
    g2 = _make("현재 목표", priority=0.8)
    ctx.add_goal(g1)
    ctx.add_goal(g2)
    # set_active 없이 직접 설정 (g2를 active 상태로 유지)
    ctx.last_active_goal_id = g1.goal_id
    # g1을 completed로 직접 변경 (last_active_goal_id는 여전히 g1)
    ctx.goal_stack.update(g1.goal_id, status="completed")
    result = ctx.get_active_goal()
    # g2가 최고 active → fallback 반환
    assert result is not None
    assert result.goal_id == g2.goal_id

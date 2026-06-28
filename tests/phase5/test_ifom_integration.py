"""Phase 5 STEP 2 — IFOM + STEP 1 통합 테스트."""
from __future__ import annotations

import time

import pytest

from app.memory.goal import Goal, make_goal
from app.memory.goal_stack import GoalStackConfig
from app.memory.ifom import IFOMConfig, IFOMPolicy
from app.memory.session_goal_context import SessionGoalContext
from app.memory.store import InMemorySessionGoalStore


def _old(status: str, priority: float, age: float) -> Goal:
    now = time.monotonic()
    g = make_goal(title=f"{status} goal", source="user_explicit", priority=priority)
    return g.model_copy(update={"status": status, "last_used_at": now - age})


# ---------------------------------------------------------------------------
# SessionGoalContext + IFOMPolicy 통합
# ---------------------------------------------------------------------------

def test_cleanup_integrates_with_session_context():
    cfg = IFOMConfig(active_ttl_seconds=100.0)
    policy = IFOMPolicy(cfg)
    ctx = SessionGoalContext.for_session("sess_int")

    fresh = make_goal(title="fresh", source="user_explicit", priority=0.8)
    stale = _old("active", 0.8, age=200.0)
    ctx.add_goal(fresh)
    ctx.add_goal(stale)

    decisions = policy.cleanup_expired(ctx)
    assert len(decisions) == 2

    by_id = {d.goal_id: d for d in decisions}
    assert by_id[fresh.goal_id].action == "keep"
    assert by_id[stale.goal_id].action == "mark_expired"

    assert ctx.goal_stack.get(stale.goal_id).status == "expired"
    assert len(ctx.goal_stack) == 2  # 둘 다 stack에 남음


@pytest.mark.asyncio
async def test_cleanup_integrates_with_store():
    store = InMemorySessionGoalStore()
    policy = IFOMPolicy(IFOMConfig(completed_ttl_seconds=50.0))

    ctx = await store.get_or_create_session("sess_store")
    g = _old("completed", 0.8, age=100.0)
    ctx.add_goal(g)

    decisions = policy.cleanup_expired(ctx)
    assert decisions[0].action == "remove"
    assert ctx.goal_stack.get(g.goal_id) is None


def test_cleanup_5_goals_partial_expiry():
    """active 5개 중 expire 2 + remove 1 → 2 active 남음."""
    cfg = IFOMConfig(
        active_ttl_seconds=100.0,
        completed_ttl_seconds=50.0,
        low_priority_ttl_seconds=20.0,
    )
    policy = IFOMPolicy(cfg)
    ctx = SessionGoalContext.for_session("sess_5")

    a1 = make_goal(title="fresh-1", source="user_explicit", priority=0.8)
    a2 = make_goal(title="fresh-2", source="user_explicit", priority=0.7)
    s1 = _old("active", 0.8, age=200.0)
    s2 = _old("active", 0.8, age=300.0)
    c1 = _old("completed", 0.8, age=100.0)

    for g in [a1, a2, s1, s2, c1]:
        ctx.add_goal(g)

    decisions = policy.cleanup_expired(ctx)
    assert len(decisions) == 5

    expired_count = sum(1 for d in decisions if d.action == "mark_expired")
    removed_count = sum(1 for d in decisions if d.action == "remove")
    kept_count = sum(1 for d in decisions if d.action == "keep")

    assert expired_count == 2
    assert removed_count == 1
    assert kept_count == 2

    # stack: 2 active + 2 expired = 4
    assert len(ctx.goal_stack) == 4
    assert len(ctx.goal_stack.list_by_status("active")) == 2


def test_cleanup_is_orthogonal_to_eviction():
    """cleanup과 eviction은 독립적으로 동작."""
    stack_cfg = GoalStackConfig(max_depth=3)
    policy = IFOMPolicy(IFOMConfig(active_ttl_seconds=100.0))
    ctx = SessionGoalContext.for_session("sess_ortho", stack_cfg)

    for i in range(3):
        ctx.add_goal(make_goal(title=f"목표 {i}", source="user_explicit", priority=0.5))
    assert len(ctx.goal_stack) == 3

    # eviction 발생
    ctx.add_goal(make_goal(title="4번째", source="user_explicit", priority=0.8))
    assert len(ctx.goal_stack) == 3

    # cleanup → 모두 fresh이므로 keep
    decisions = policy.cleanup_expired(ctx)
    assert all(d.action == "keep" for d in decisions)
    assert len(ctx.goal_stack) == 3


# ---------------------------------------------------------------------------
# GoalStack 자동 cleanup 부작용 0건 검증
# ---------------------------------------------------------------------------

def test_goalstack_add_no_cleanup_side_effect():
    """GoalStack.add()는 IFOM cleanup을 트리거하지 않음."""
    ctx = SessionGoalContext.for_session("sess_add_noc")
    stale = _old("active", 0.8, age=100000.0)
    ctx.add_goal(stale)
    # add가 cleanup을 트리거했다면 status가 expired로 바뀌었을 것
    assert ctx.goal_stack.get(stale.goal_id).status == "active"


def test_goalstack_get_no_cleanup_side_effect():
    """GoalStack.get()은 IFOM cleanup을 트리거하지 않음."""
    ctx = SessionGoalContext.for_session("sess_get_noc")
    stale = _old("active", 0.8, age=100000.0)
    ctx.add_goal(stale)
    result = ctx.goal_stack.get(stale.goal_id)
    assert result.status == "active"


def test_goalstack_list_all_no_cleanup_side_effect():
    """GoalStack.list_all()은 IFOM cleanup을 트리거하지 않음."""
    ctx = SessionGoalContext.for_session("sess_list_noc")
    stale = _old("active", 0.8, age=100000.0)
    ctx.add_goal(stale)
    all_goals = ctx.goal_stack.list_all()
    assert all(g.status == "active" for g in all_goals)


def test_goalstack_get_active_no_cleanup_side_effect():
    """GoalStack.get_active()는 IFOM cleanup을 트리거하지 않음."""
    ctx = SessionGoalContext.for_session("sess_getactive_noc")
    stale = _old("active", 0.8, age=100000.0)
    ctx.add_goal(stale)
    active = ctx.goal_stack.get_active()
    assert len(active) == 1
    assert active[0].status == "active"


def test_no_cleanup_without_explicit_call():
    """cleanup_expired() 미호출 시 TTL 초과해도 상태 변경 없음."""
    ctx = SessionGoalContext.for_session("sess_no_explicit")
    stale = _old("active", 0.8, age=100000.0)
    ctx.add_goal(stale)

    # 다양한 stack 메서드 호출 후에도 active 유지
    _ = ctx.goal_stack.get(stale.goal_id)
    _ = ctx.goal_stack.list_all()
    _ = ctx.goal_stack.get_active()
    _ = ctx.goal_stack.get_top_goal()
    _ = ctx.goal_stack.list_by_status("active")

    assert ctx.goal_stack.get(stale.goal_id).status == "active"


# ---------------------------------------------------------------------------
# trace 스코프 cleanup
# ---------------------------------------------------------------------------

def test_cleanup_works_on_trace_context():
    policy = IFOMPolicy(IFOMConfig(active_ttl_seconds=100.0))
    ctx = SessionGoalContext.for_trace("trace_001")

    stale = _old("active", 0.8, age=200.0)
    ctx.add_goal(stale)

    decisions = policy.cleanup_expired(ctx)
    assert decisions[0].action == "mark_expired"
    assert ctx.goal_stack.get(stale.goal_id).status == "expired"

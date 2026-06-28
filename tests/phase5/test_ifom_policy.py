"""Phase 5 STEP 2 — IFOMPolicy 단위 테스트."""
from __future__ import annotations

import time

import pytest

from app.memory.goal import Goal, make_goal
from app.memory.ifom import IFOMConfig, IFOMDecision, IFOMPolicy
from app.memory.session_goal_context import SessionGoalContext


def _goal_with_age(status: str, priority: float, age: float) -> Goal:
    """age초 전에 마지막 사용된 goal을 시뮬레이션."""
    now = time.monotonic()
    g = make_goal(title=f"{status}-goal", source="user_explicit", priority=priority)
    return g.model_copy(update={"status": status, "last_used_at": now - age})


# ---------------------------------------------------------------------------
# _is_low_priority
# ---------------------------------------------------------------------------

def test_is_low_priority_zero():
    g = make_goal(title="t", source="user_explicit", priority=0.0)
    assert IFOMPolicy()._is_low_priority(g) is True


def test_is_low_priority_boundary_0_3():
    g = make_goal(title="t", source="user_explicit", priority=0.3)
    assert IFOMPolicy()._is_low_priority(g) is True  # 0.3 포함


def test_is_low_priority_just_above_boundary():
    g = make_goal(title="t", source="user_explicit", priority=0.31)
    assert IFOMPolicy()._is_low_priority(g) is False


def test_is_low_priority_mid():
    g = make_goal(title="t", source="user_explicit", priority=0.5)
    assert IFOMPolicy()._is_low_priority(g) is False


def test_is_low_priority_max():
    g = make_goal(title="t", source="user_explicit", priority=1.0)
    assert IFOMPolicy()._is_low_priority(g) is False


def test_is_low_priority_custom_threshold():
    policy = IFOMPolicy(IFOMConfig(low_priority_threshold=0.5))
    g_at = make_goal(title="t", source="user_explicit", priority=0.5)
    g_above = make_goal(title="t", source="user_explicit", priority=0.51)
    assert policy._is_low_priority(g_at) is True
    assert policy._is_low_priority(g_above) is False


# ---------------------------------------------------------------------------
# _get_ttl_for_goal
# ---------------------------------------------------------------------------

def test_get_ttl_active_high_priority():
    policy = IFOMPolicy()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    assert policy._get_ttl_for_goal(g) == 3600.0


def test_get_ttl_paused_high_priority():
    policy = IFOMPolicy()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"status": "paused"})
    assert policy._get_ttl_for_goal(g) == 3600.0


def test_get_ttl_completed_high_priority():
    policy = IFOMPolicy()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"status": "completed"})
    assert policy._get_ttl_for_goal(g) == 600.0


def test_get_ttl_active_low_priority():
    policy = IFOMPolicy()
    g = make_goal(title="t", source="system", priority=0.2)
    assert policy._get_ttl_for_goal(g) == 300.0


def test_get_ttl_completed_low_priority():
    """completed + low priority → min(600, 300) = 300."""
    policy = IFOMPolicy()
    g = make_goal(title="t", source="system", priority=0.2)
    g = g.model_copy(update={"status": "completed"})
    assert policy._get_ttl_for_goal(g) == 300.0


def test_get_ttl_expired_is_inf():
    policy = IFOMPolicy()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"status": "expired"})
    assert policy._get_ttl_for_goal(g) == float("inf")


# ---------------------------------------------------------------------------
# adjust_ttl_with_rpe_hook (no-op)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base_ttl", [0.001, 100.0, 3600.0, 99999.0])
def test_rpe_hook_is_noop(base_ttl: float):
    policy = IFOMPolicy()
    g = make_goal(title="t", source="user_explicit")
    assert policy.adjust_ttl_with_rpe_hook(g, base_ttl) == base_ttl


# ---------------------------------------------------------------------------
# evaluate_goal — 기본 시나리오
# ---------------------------------------------------------------------------

def test_evaluate_active_within_ttl():
    policy = IFOMPolicy()
    g = _goal_with_age("active", 0.8, age=1800.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "keep"
    assert d.reason == "active_within_ttl"


def test_evaluate_active_ttl_exceeded():
    policy = IFOMPolicy()
    g = _goal_with_age("active", 0.8, age=3601.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "mark_expired"
    assert d.reason == "active_ttl_exceeded"


def test_evaluate_paused_ttl_exceeded():
    policy = IFOMPolicy()
    g = _goal_with_age("paused", 0.8, age=3601.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "mark_expired"
    assert d.reason == "paused_ttl_exceeded"


def test_evaluate_completed_ttl_exceeded():
    policy = IFOMPolicy()
    g = _goal_with_age("completed", 0.8, age=601.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "remove"
    assert d.reason == "completed_ttl_exceeded"


def test_evaluate_low_priority_ttl_exceeded():
    policy = IFOMPolicy()
    g = _goal_with_age("active", 0.2, age=301.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "mark_expired"
    assert d.reason == "low_priority_ttl_exceeded"


def test_evaluate_low_priority_within_ttl():
    policy = IFOMPolicy()
    g = _goal_with_age("active", 0.2, age=100.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "keep"
    assert d.reason == "low_priority_kept"


def test_evaluate_already_expired():
    policy = IFOMPolicy()
    g = _goal_with_age("expired", 0.8, age=99999.0)
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "keep"
    assert d.reason == "already_expired"


def test_evaluate_completed_low_priority_ttl_exceeded_is_remove():
    """completed + low priority → TTL 초과 시 remove (completed 분기가 우선)."""
    policy = IFOMPolicy()
    g = _goal_with_age("completed", 0.2, age=400.0)  # low_ttl=300, comp_ttl=600, effective=min=300 → age>300
    d = policy.evaluate_goal(g, time.monotonic())
    assert d.action == "remove"
    assert d.reason == "completed_ttl_exceeded"


def test_evaluate_age_seconds_populated():
    policy = IFOMPolicy()
    g = _goal_with_age("active", 0.8, age=1800.0)
    now = time.monotonic()
    d = policy.evaluate_goal(g, now)
    assert abs(d.age_seconds - 1800.0) < 1.0  # 1초 허용 (helper + test 사이 시간)


# ---------------------------------------------------------------------------
# evaluate_goal — 경계값
# ---------------------------------------------------------------------------

def test_evaluate_active_at_exact_ttl_boundary_keep():
    """age == active_ttl → keep (<=)."""
    policy = IFOMPolicy(IFOMConfig(active_ttl_seconds=3600.0))
    now = time.monotonic()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"last_used_at": now - 3600.0, "status": "active"})
    d = policy.evaluate_goal(g, now)
    assert d.action == "keep"
    assert d.reason == "active_within_ttl"


def test_evaluate_active_just_over_ttl_mark_expired():
    """age > active_ttl → mark_expired."""
    policy = IFOMPolicy(IFOMConfig(active_ttl_seconds=3600.0))
    now = time.monotonic()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"last_used_at": now - 3600.001, "status": "active"})
    d = policy.evaluate_goal(g, now)
    assert d.action == "mark_expired"


def test_evaluate_completed_at_exact_ttl_boundary_keep():
    """age == completed_ttl → keep."""
    policy = IFOMPolicy(IFOMConfig(completed_ttl_seconds=600.0))
    now = time.monotonic()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"last_used_at": now - 600.0, "status": "completed"})
    d = policy.evaluate_goal(g, now)
    assert d.action == "keep"


def test_evaluate_completed_just_over_ttl_remove():
    """age > completed_ttl → remove."""
    policy = IFOMPolicy(IFOMConfig(completed_ttl_seconds=600.0))
    now = time.monotonic()
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    g = g.model_copy(update={"last_used_at": now - 600.001, "status": "completed"})
    d = policy.evaluate_goal(g, now)
    assert d.action == "remove"


def test_evaluate_low_priority_at_exact_ttl_boundary_keep():
    """age == low_priority_ttl → keep."""
    policy = IFOMPolicy(IFOMConfig(low_priority_ttl_seconds=300.0))
    now = time.monotonic()
    g = make_goal(title="t", source="system", priority=0.2)
    g = g.model_copy(update={"last_used_at": now - 300.0, "status": "active"})
    d = policy.evaluate_goal(g, now)
    assert d.action == "keep"
    assert d.reason == "low_priority_kept"


def test_evaluate_low_priority_just_over_ttl_mark_expired():
    """age > low_priority_ttl → mark_expired."""
    policy = IFOMPolicy(IFOMConfig(low_priority_ttl_seconds=300.0))
    now = time.monotonic()
    g = make_goal(title="t", source="system", priority=0.2)
    g = g.model_copy(update={"last_used_at": now - 300.001, "status": "active"})
    d = policy.evaluate_goal(g, now)
    assert d.action == "mark_expired"


# ---------------------------------------------------------------------------
# cleanup_expired
# ---------------------------------------------------------------------------

def test_cleanup_empty_stack():
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_empty")
    decisions = policy.cleanup_expired(ctx)
    assert decisions == []


def test_cleanup_all_within_ttl_keep():
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_keep")
    g = _goal_with_age("active", 0.8, age=60.0)
    ctx.add_goal(g)
    decisions = policy.cleanup_expired(ctx)
    assert len(decisions) == 1
    assert decisions[0].action == "keep"
    assert len(ctx.goal_stack) == 1


def test_cleanup_active_ttl_exceeded_marks_expired_in_stack():
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_exp")
    g = _goal_with_age("active", 0.8, age=3700.0)
    ctx.add_goal(g)
    decisions = policy.cleanup_expired(ctx)
    assert decisions[0].action == "mark_expired"
    # goal still in stack but status=expired
    assert ctx.goal_stack.get(g.goal_id) is not None
    assert ctx.goal_stack.get(g.goal_id).status == "expired"


def test_cleanup_completed_ttl_exceeded_removes_from_stack():
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_rem")
    g = _goal_with_age("completed", 0.8, age=700.0)
    ctx.add_goal(g)
    decisions = policy.cleanup_expired(ctx)
    assert decisions[0].action == "remove"
    assert ctx.goal_stack.get(g.goal_id) is None
    assert len(ctx.goal_stack) == 0


def test_cleanup_mixed_scenario():
    """active expire + completed remove + fresh keep."""
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_mix")
    g_old_active = _goal_with_age("active", 0.8, age=3700.0)
    g_old_completed = _goal_with_age("completed", 0.8, age=700.0)
    g_fresh = _goal_with_age("active", 0.8, age=60.0)
    ctx.add_goal(g_old_active)
    ctx.add_goal(g_old_completed)
    ctx.add_goal(g_fresh)

    decisions = policy.cleanup_expired(ctx)
    assert len(decisions) == 3

    by_id = {d.goal_id: d for d in decisions}
    assert by_id[g_old_active.goal_id].action == "mark_expired"
    assert by_id[g_old_completed.goal_id].action == "remove"
    assert by_id[g_fresh.goal_id].action == "keep"

    assert ctx.goal_stack.get(g_old_active.goal_id).status == "expired"
    assert ctx.goal_stack.get(g_old_completed.goal_id) is None
    assert ctx.goal_stack.get(g_fresh.goal_id) is not None
    assert len(ctx.goal_stack) == 2


def test_cleanup_now_injection_keep():
    """now 주입 — TTL 미초과."""
    policy = IFOMPolicy(IFOMConfig(active_ttl_seconds=100.0))
    ctx = SessionGoalContext.for_session("sess_now1")
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    ctx.add_goal(g)
    decisions = policy.cleanup_expired(ctx, now=g.last_used_at + 50.0)
    assert decisions[0].action == "keep"


def test_cleanup_now_injection_expire():
    """now 주입 — TTL 초과."""
    policy = IFOMPolicy(IFOMConfig(active_ttl_seconds=100.0))
    ctx = SessionGoalContext.for_session("sess_now2")
    g = make_goal(title="t", source="user_explicit", priority=0.8)
    ctx.add_goal(g)
    decisions = policy.cleanup_expired(ctx, now=g.last_used_at + 150.0)
    assert decisions[0].action == "mark_expired"


def test_cleanup_updates_context_updated_at_when_changed():
    """변경 있을 때 context.updated_at 갱신."""
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_ua")
    g = _goal_with_age("active", 0.8, age=3700.0)
    ctx.add_goal(g)
    old_updated = ctx.updated_at
    now = time.monotonic()
    policy.cleanup_expired(ctx, now=now)
    assert ctx.updated_at >= old_updated


def test_cleanup_no_update_when_nothing_changed():
    """변경 없을 때 context.updated_at 갱신 안 함."""
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_nochg")
    g = _goal_with_age("active", 0.8, age=60.0)
    ctx.add_goal(g)
    after_add = ctx.updated_at
    policy.cleanup_expired(ctx)
    assert ctx.updated_at == after_add


def test_cleanup_decisions_consistent_with_mutations():
    """decision 리스트와 실제 stack 상태가 일관."""
    policy = IFOMPolicy()
    ctx = SessionGoalContext.for_session("sess_cons")
    goals = [
        _goal_with_age("active", 0.8, age=3700.0),
        _goal_with_age("active", 0.8, age=60.0),
        _goal_with_age("completed", 0.8, age=700.0),
    ]
    for g in goals:
        ctx.add_goal(g)

    decisions = policy.cleanup_expired(ctx)
    for d in decisions:
        if d.action == "remove":
            assert ctx.goal_stack.get(d.goal_id) is None
        elif d.action == "mark_expired":
            goal_in_stack = ctx.goal_stack.get(d.goal_id)
            assert goal_in_stack is not None
            assert goal_in_stack.status == "expired"
        elif d.action == "keep":
            assert ctx.goal_stack.get(d.goal_id) is not None

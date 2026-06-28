"""Phase 5 STEP 1 — GoalStack 단위 테스트."""
from __future__ import annotations

import math
import time
from unittest.mock import patch

import pytest

from app.memory.goal import Goal, make_goal
from app.memory.goal_stack import GoalStack, GoalStackConfig


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make(title: str = "목표", source: str = "user_explicit", priority: float | None = None) -> Goal:
    return make_goal(title=title, source=source, priority=priority)  # type: ignore[arg-type]


def _make_with_status(title: str, status: str, priority: float = 0.5) -> Goal:
    g = make_goal(title=title, source="system", priority=priority)
    return g.model_copy(update={"status": status})


# ---------------------------------------------------------------------------
# 기본 동작
# ---------------------------------------------------------------------------

def test_add_and_get():
    stack = GoalStack()
    g = _make("목표 A")
    stack.add(g)
    assert stack.get(g.goal_id) == g


def test_add_duplicate_raises_value_error():
    stack = GoalStack()
    g = _make("목표 A")
    stack.add(g)
    with pytest.raises(ValueError, match="already exists"):
        stack.add(g)


def test_len_after_add():
    stack = GoalStack()
    assert len(stack) == 0
    g = _make()
    stack.add(g)
    assert len(stack) == 1


def test_contains():
    stack = GoalStack()
    g = _make()
    stack.add(g)
    assert g.goal_id in stack
    assert "nonexistent_id" not in stack


def test_remove_existing():
    stack = GoalStack()
    g = _make()
    stack.add(g)
    removed = stack.remove(g.goal_id)
    assert removed == g
    assert len(stack) == 0


def test_remove_nonexistent_returns_none():
    stack = GoalStack()
    assert stack.remove("no_such_id") is None


def test_get_nonexistent_returns_none():
    stack = GoalStack()
    assert stack.get("no_such_id") is None


# ---------------------------------------------------------------------------
# update / touch
# ---------------------------------------------------------------------------

def test_update_status():
    stack = GoalStack()
    g = _make()
    stack.add(g)
    updated = stack.update(g.goal_id, status="paused")
    assert updated.status == "paused"
    assert stack.get(g.goal_id).status == "paused"


def test_update_sets_updated_at():
    stack = GoalStack()
    g = _make()
    stack.add(g)
    before = time.monotonic()
    updated = stack.update(g.goal_id, status="completed")
    after = time.monotonic()
    assert before <= updated.updated_at <= after


def test_update_does_not_change_last_used_at():
    stack = GoalStack()
    g = _make()
    stack.add(g)
    original_last_used = g.last_used_at
    updated = stack.update(g.goal_id, status="paused")
    assert updated.last_used_at == original_last_used


def test_update_nonexistent_raises_key_error():
    stack = GoalStack()
    with pytest.raises(KeyError):
        stack.update("no_such_id", status="paused")


def test_touch_updates_last_used_at():
    stack = GoalStack()
    g = _make()
    stack.add(g)
    before = time.monotonic()
    touched = stack.touch(g.goal_id)
    after = time.monotonic()
    assert before <= touched.last_used_at <= after


def test_touch_nonexistent_raises_key_error():
    stack = GoalStack()
    with pytest.raises(KeyError):
        stack.touch("no_such_id")


# ---------------------------------------------------------------------------
# list_all / list_by_status / get_active / get_top_goal
# ---------------------------------------------------------------------------

def test_list_all():
    stack = GoalStack()
    goals = [_make(f"목표 {i}") for i in range(3)]
    for g in goals:
        stack.add(g)
    assert len(stack.list_all()) == 3


def test_list_by_status_filters_correctly():
    stack = GoalStack()
    active_goal = _make("active 목표")
    paused_goal = _make_with_status("paused 목표", "paused")
    stack.add(active_goal)
    stack.add(paused_goal)
    active_list = stack.list_by_status("active")
    assert len(active_list) == 1
    assert active_list[0].goal_id == active_goal.goal_id


def test_get_active_returns_only_active():
    stack = GoalStack()
    a = _make("active", priority=0.8)
    p = _make_with_status("paused", "paused", priority=0.9)
    stack.add(a)
    stack.add(p)
    active = stack.get_active()
    assert all(g.status == "active" for g in active)
    assert len(active) == 1


def test_get_active_sorted_by_score_desc():
    stack = GoalStack()
    low = _make("낮은 우선순위", priority=0.2)
    high = _make("높은 우선순위", priority=0.9)
    stack.add(low)
    stack.add(high)
    active = stack.get_active()
    assert active[0].goal_id == high.goal_id


def test_get_top_goal_returns_highest_priority():
    stack = GoalStack()
    low = _make("낮은", priority=0.2)
    high = _make("높은", priority=0.9)
    stack.add(low)
    stack.add(high)
    top = stack.get_top_goal()
    assert top is not None
    assert top.goal_id == high.goal_id


def test_get_top_goal_returns_none_when_no_active():
    stack = GoalStack()
    g = _make_with_status("완료", "completed")
    stack.add(g)
    assert stack.get_top_goal() is None


# ---------------------------------------------------------------------------
# Effective score (exponential decay)
# ---------------------------------------------------------------------------

def test_new_goal_effective_score_equals_priority():
    stack = GoalStack()
    g = _make(priority=0.7)
    stack.add(g)
    now = g.last_used_at  # 방금 생성된 시점
    score = stack._effective_score(g, now=now)
    assert abs(score - 0.7) < 1e-9


def test_effective_score_decreases_with_age():
    stack = GoalStack()
    g = _make(priority=1.0)
    stack.add(g)
    score_now = stack._effective_score(g, now=g.last_used_at)
    score_later = stack._effective_score(g, now=g.last_used_at + 3600.0)
    assert score_later < score_now


def test_effective_score_one_hour_decay():
    """λ = 1/3600 → 1시간 후 exp(-1) ≈ 0.368."""
    stack = GoalStack()
    g = _make(priority=1.0)
    stack.add(g)
    score = stack._effective_score(g, now=g.last_used_at + 3600.0)
    expected = math.exp(-1.0)  # ≈ 0.36788
    assert abs(score - expected) < 0.01


def test_touch_resets_recency_decay():
    stack = GoalStack()
    g = _make(priority=1.0)
    stack.add(g)
    # 오래된 goal처럼 시뮬레이션
    old_last_used = g.last_used_at - 3600.0
    stack._goals[g.goal_id] = g.model_copy(update={"last_used_at": old_last_used})

    decayed_score = stack._effective_score(stack.get(g.goal_id), now=time.monotonic())
    assert decayed_score < 0.5  # 많이 감소했어야 함

    touched = stack.touch(g.goal_id)
    fresh_score = stack._effective_score(touched, now=touched.last_used_at)
    assert abs(fresh_score - 1.0) < 1e-9


def test_custom_lambda_faster_decay():
    """더 큰 λ → 더 빠른 decay."""
    fast_config = GoalStackConfig(recency_decay_lambda=1.0 / 60.0)  # 1분 반감
    stack = GoalStack(fast_config)
    g = _make(priority=1.0)
    stack.add(g)
    score_60s = stack._effective_score(g, now=g.last_used_at + 60.0)
    expected = math.exp(-1.0)
    assert abs(score_60s - expected) < 0.01


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------

def test_add_up_to_max_depth_no_eviction():
    stack = GoalStack()
    for i in range(7):
        evicted = stack.add(_make(f"목표 {i}"))
        assert evicted is None
    assert len(stack) == 7


def test_add_beyond_max_depth_evicts_one():
    stack = GoalStack()
    for i in range(7):
        stack.add(_make(f"목표 {i}"))
    evicted = stack.add(_make("8번째"))
    assert evicted is not None
    assert len(stack) == 7


def test_eviction_prefers_expired_over_completed():
    stack = GoalStack(GoalStackConfig(max_depth=2))
    expired_g = _make_with_status("만료", "expired", priority=0.9)
    completed_g = _make_with_status("완료", "completed", priority=0.1)
    stack.add(expired_g)
    stack.add(completed_g)
    evicted = stack.add(_make("신규"))
    assert evicted.goal_id == expired_g.goal_id


def test_eviction_prefers_completed_over_paused():
    stack = GoalStack(GoalStackConfig(max_depth=2))
    completed_g = _make_with_status("완료", "completed", priority=0.9)
    paused_g = _make_with_status("보류", "paused", priority=0.1)
    stack.add(completed_g)
    stack.add(paused_g)
    evicted = stack.add(_make("신규"))
    assert evicted.goal_id == completed_g.goal_id


def test_eviction_prefers_paused_over_active():
    stack = GoalStack(GoalStackConfig(max_depth=2))
    active_g = _make("활성", priority=0.3)
    paused_g = _make_with_status("보류", "paused", priority=0.9)
    stack.add(active_g)
    stack.add(paused_g)
    evicted = stack.add(_make("신규"))
    assert evicted.goal_id == paused_g.goal_id


def test_eviction_within_same_status_removes_lowest_score():
    """같은 상태 내 score 낮은 것 제거 (버그 회피: NOT highest score)."""
    stack = GoalStack(GoalStackConfig(max_depth=2))
    high_score = _make_with_status("높은 점수", "completed", priority=0.9)
    low_score = _make_with_status("낮은 점수", "completed", priority=0.1)
    stack.add(high_score)
    stack.add(low_score)
    evicted = stack.add(_make("신규"))
    # 0.1짜리가 제거되어야 함 (NOT 0.9)
    assert evicted.goal_id == low_score.goal_id


def test_eviction_active_only_removes_lowest_active():
    """active만 있을 때 max_depth 초과 시 lowest active 제거."""
    stack = GoalStack(GoalStackConfig(max_depth=2))
    high = _make("높은", priority=0.9)
    low = _make("낮은", priority=0.1)
    stack.add(high)
    stack.add(low)
    evicted = stack.add(_make("신규"))
    assert evicted.goal_id == low.goal_id


# ---------------------------------------------------------------------------
# GoalStackConfig
# ---------------------------------------------------------------------------

def test_goalstackconfig_defaults():
    cfg = GoalStackConfig()
    assert cfg.max_depth == 7
    assert abs(cfg.recency_decay_lambda - 1.0 / 3600.0) < 1e-12


def test_goalstackconfig_custom_max_depth():
    stack = GoalStack(GoalStackConfig(max_depth=3))
    for i in range(3):
        stack.add(_make(f"목표 {i}"))
    assert len(stack) == 3
    evicted = stack.add(_make("4번째"))
    assert evicted is not None
    assert len(stack) == 3

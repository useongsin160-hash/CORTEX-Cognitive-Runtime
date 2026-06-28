"""Phase 5 STEP 1 — InMemorySessionGoalStore 단위 테스트."""
from __future__ import annotations

import pytest

from app.memory.goal import make_goal
from app.memory.goal_stack import GoalStackConfig
from app.memory.session_goal_context import SessionGoalContext
from app.memory.store import InMemorySessionGoalStore, SessionGoalStore


# ---------------------------------------------------------------------------
# get_or_create_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_or_create_session_creates_new():
    store = InMemorySessionGoalStore()
    ctx = await store.get_or_create_session("sess_001")
    assert isinstance(ctx, SessionGoalContext)
    assert ctx.scope_type == "session"
    assert ctx.scope_id == "sess_001"


@pytest.mark.asyncio
async def test_get_or_create_session_returns_same_instance():
    store = InMemorySessionGoalStore()
    ctx1 = await store.get_or_create_session("sess_001")
    ctx2 = await store.get_or_create_session("sess_001")
    assert ctx1 is ctx2


@pytest.mark.asyncio
async def test_get_or_create_session_different_ids_different_instances():
    store = InMemorySessionGoalStore()
    ctx1 = await store.get_or_create_session("sess_001")
    ctx2 = await store.get_or_create_session("sess_002")
    assert ctx1 is not ctx2


# ---------------------------------------------------------------------------
# get_or_create_trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_or_create_trace_creates_new():
    store = InMemorySessionGoalStore()
    ctx = await store.get_or_create_trace("trace_001")
    assert isinstance(ctx, SessionGoalContext)
    assert ctx.scope_type == "trace"
    assert ctx.scope_id == "trace_001"


@pytest.mark.asyncio
async def test_get_or_create_trace_returns_same_instance():
    store = InMemorySessionGoalStore()
    ctx1 = await store.get_or_create_trace("trace_001")
    ctx2 = await store.get_or_create_trace("trace_001")
    assert ctx1 is ctx2


# ---------------------------------------------------------------------------
# session/trace 스코프 분리
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_and_trace_same_id_are_different():
    """같은 ID라도 session과 trace는 다른 컨텍스트."""
    store = InMemorySessionGoalStore()
    ctx_s = await store.get_or_create_session("same_id")
    ctx_t = await store.get_or_create_trace("same_id")
    assert ctx_s is not ctx_t
    assert ctx_s.scope_type == "session"
    assert ctx_t.scope_type == "trace"


@pytest.mark.asyncio
async def test_session_goal_does_not_appear_in_trace():
    store = InMemorySessionGoalStore()
    ctx_s = await store.get_or_create_session("scope_test")
    ctx_t = await store.get_or_create_trace("scope_test")
    g = make_goal(title="세션 목표", source="user_explicit")
    ctx_s.add_goal(g)
    assert len(ctx_s.goal_stack) == 1
    assert len(ctx_t.goal_stack) == 0


# ---------------------------------------------------------------------------
# delete_session / delete_trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_session_returns_true_when_exists():
    store = InMemorySessionGoalStore()
    await store.get_or_create_session("sess_del")
    result = await store.delete_session("sess_del")
    assert result is True


@pytest.mark.asyncio
async def test_delete_session_returns_false_when_not_exists():
    store = InMemorySessionGoalStore()
    result = await store.delete_session("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_delete_session_actually_removes_it():
    store = InMemorySessionGoalStore()
    await store.get_or_create_session("sess_del2")
    await store.delete_session("sess_del2")
    sessions = await store.list_sessions()
    assert "sess_del2" not in sessions


@pytest.mark.asyncio
async def test_delete_trace_returns_true_when_exists():
    store = InMemorySessionGoalStore()
    await store.get_or_create_trace("trace_del")
    result = await store.delete_trace("trace_del")
    assert result is True


@pytest.mark.asyncio
async def test_delete_trace_returns_false_when_not_exists():
    store = InMemorySessionGoalStore()
    result = await store.delete_trace("nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# list_sessions / list_traces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_sessions_empty_initially():
    store = InMemorySessionGoalStore()
    assert await store.list_sessions() == []


@pytest.mark.asyncio
async def test_list_sessions_returns_all():
    store = InMemorySessionGoalStore()
    await store.get_or_create_session("sess_a")
    await store.get_or_create_session("sess_b")
    sessions = await store.list_sessions()
    assert set(sessions) == {"sess_a", "sess_b"}


@pytest.mark.asyncio
async def test_list_traces_empty_initially():
    store = InMemorySessionGoalStore()
    assert await store.list_traces() == []


@pytest.mark.asyncio
async def test_list_traces_returns_all():
    store = InMemorySessionGoalStore()
    await store.get_or_create_trace("trace_a")
    await store.get_or_create_trace("trace_b")
    traces = await store.list_traces()
    assert set(traces) == {"trace_a", "trace_b"}


@pytest.mark.asyncio
async def test_list_sessions_does_not_include_traces():
    store = InMemorySessionGoalStore()
    await store.get_or_create_session("sess_x")
    await store.get_or_create_trace("trace_x")
    sessions = await store.list_sessions()
    traces = await store.list_traces()
    assert "trace_x" not in sessions
    assert "sess_x" not in traces


# ---------------------------------------------------------------------------
# custom config 전파
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_passes_config_to_session_context():
    cfg = GoalStackConfig(max_depth=3)
    store = InMemorySessionGoalStore(config=cfg)
    ctx = await store.get_or_create_session("sess_cfg")
    assert ctx.goal_stack._config.max_depth == 3


@pytest.mark.asyncio
async def test_store_passes_config_to_trace_context():
    cfg = GoalStackConfig(max_depth=2)
    store = InMemorySessionGoalStore(config=cfg)
    ctx = await store.get_or_create_trace("trace_cfg")
    assert ctx.goal_stack._config.max_depth == 2


# ---------------------------------------------------------------------------
# SessionGoalStore Protocol 호환
# ---------------------------------------------------------------------------

def test_inmemory_store_satisfies_protocol():
    """InMemorySessionGoalStore가 SessionGoalStore Protocol을 구현함을 런타임 검증."""
    store = InMemorySessionGoalStore()
    required_methods = [
        "get_or_create_session",
        "get_or_create_trace",
        "delete_session",
        "delete_trace",
        "list_sessions",
        "list_traces",
    ]
    for method in required_methods:
        assert hasattr(store, method), f"Missing method: {method}"
        assert callable(getattr(store, method)), f"Not callable: {method}"

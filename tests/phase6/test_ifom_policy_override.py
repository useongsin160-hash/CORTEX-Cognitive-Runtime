"""Phase 6 STEP 4 — IFOMPolicy ttl_override_resolver integration tests.

Tests that IFOMPolicy.adjust_ttl_with_rpe_hook calls the resolver
and returns the override value, with no-op fallback when resolver is None
or returns None.
"""
from __future__ import annotations

from app.memory.goal import make_goal
from app.memory.ifom import IFOMConfig, IFOMPolicy
from app.rpe.ifom_store import InMemoryIFOMTTLOverrideStore


# ---------------------------------------------------------------------------
# No resolver (Phase 5 STEP 2 no-op backward compat)
# ---------------------------------------------------------------------------


def _active_goal(
    session_id: str = "sess-1", category: str | None = "coding", priority: float = 0.8
):
    return make_goal(
        title="test goal",
        source="user_explicit",
        session_id=session_id,
        category=category,
        priority=priority,
    )


def test_no_resolver_returns_base_ttl():
    policy = IFOMPolicy()
    goal = _active_goal()
    assert policy.adjust_ttl_with_rpe_hook(goal, 3600.0) == 3600.0


def test_no_resolver_returns_any_base_ttl():
    policy = IFOMPolicy()
    goal = _active_goal()
    assert policy.adjust_ttl_with_rpe_hook(goal, 1234.5) == 1234.5


# ---------------------------------------------------------------------------
# Resolver returning override
# ---------------------------------------------------------------------------


def test_resolver_returns_override_for_active():
    def resolver(session_id, category, ttl_type):
        if ttl_type == "active" and session_id == "sess-1":
            return 7200.0
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal(session_id="sess-1")
    result = policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert result == 7200.0


def test_resolver_returns_none_uses_base_ttl():
    def resolver(session_id, category, ttl_type):
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal()
    result = policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert result == 3600.0


def test_resolver_receives_session_id_and_category():
    received = {}

    def resolver(session_id, category, ttl_type):
        received["session_id"] = session_id
        received["category"] = category
        received["ttl_type"] = ttl_type
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal(session_id="sess-1", category="coding")
    policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert received["session_id"] == "sess-1"
    assert received["category"] == "coding"
    assert received["ttl_type"] == "active"


# ---------------------------------------------------------------------------
# TTL type mapping
# ---------------------------------------------------------------------------


def test_ttl_type_active_status():
    received_types = []

    def resolver(session_id, category, ttl_type):
        received_types.append(ttl_type)
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal()
    goal = goal.model_copy(update={"status": "active"})
    policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert received_types[-1] == "active"


def test_ttl_type_paused_status():
    received_types = []

    def resolver(session_id, category, ttl_type):
        received_types.append(ttl_type)
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal()
    goal = goal.model_copy(update={"status": "paused"})
    policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert received_types[-1] == "paused"


def test_ttl_type_completed_status():
    received_types = []

    def resolver(session_id, category, ttl_type):
        received_types.append(ttl_type)
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal()
    goal = goal.model_copy(update={"status": "completed"})
    policy.adjust_ttl_with_rpe_hook(goal, 600.0)
    assert received_types[-1] == "completed"


def test_ttl_type_low_priority_goal():
    """Low-priority goal uses 'low_priority' TTL type regardless of status."""
    received_types = []

    def resolver(session_id, category, ttl_type):
        received_types.append(ttl_type)
        return None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    # priority <= 0.3 → low_priority
    goal = _active_goal(priority=0.2)
    policy.adjust_ttl_with_rpe_hook(goal, 300.0)
    assert received_types[-1] == "low_priority"


# ---------------------------------------------------------------------------
# Resolver integrated with InMemoryIFOMTTLOverrideStore
# ---------------------------------------------------------------------------


def test_resolver_via_store():
    store = InMemoryIFOMTTLOverrideStore()
    store.set("sess-1", "coding", "active", override_seconds=7200.0)

    def resolver(session_id, category, ttl_type):
        override = store.read_override(session_id, category, ttl_type)
        return override.override_seconds if override is not None else None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal(session_id="sess-1", category="coding")
    result = policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert result == 7200.0


def test_resolver_via_store_no_override_uses_base():
    store = InMemoryIFOMTTLOverrideStore()

    def resolver(session_id, category, ttl_type):
        override = store.read_override(session_id, category, ttl_type)
        return override.override_seconds if override is not None else None

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal(session_id="sess-1", category="coding")
    result = policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert result == 3600.0


def test_resolver_error_falls_back_to_base_ttl():
    """Resolver errors must not propagate — fail-open."""

    def resolver(session_id, category, ttl_type):
        raise RuntimeError("store connection error")

    policy = IFOMPolicy(ttl_override_resolver=resolver)
    goal = _active_goal()
    result = policy.adjust_ttl_with_rpe_hook(goal, 3600.0)
    assert result == 3600.0


# ---------------------------------------------------------------------------
# backward compat: __init__ with only config
# ---------------------------------------------------------------------------


def test_ifom_policy_init_no_resolver():
    """IFOMPolicy(config=...) still works (no resolver = no-op)."""
    policy = IFOMPolicy(config=IFOMConfig(active_ttl_seconds=5000.0))
    goal = _active_goal()
    assert policy.adjust_ttl_with_rpe_hook(goal, 5000.0) == 5000.0

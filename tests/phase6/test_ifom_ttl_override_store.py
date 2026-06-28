"""Phase 6 STEP 4 — InMemoryIFOMTTLOverrideStore unit tests.

Tests CRUD operations, IFOMTTLOverride validation, and test helpers.
"""
from __future__ import annotations

import time

import pytest

from app.rpe.ifom_store import (
    IFOMTTLOverride,
    InMemoryIFOMTTLOverrideStore,
)


# ---------------------------------------------------------------------------
# IFOMTTLOverride validation
# ---------------------------------------------------------------------------


def _make_override(**kwargs) -> IFOMTTLOverride:
    defaults = dict(
        session_id="sess-1",
        category="coding",
        ttl_type="active",
        override_seconds=3600.0,
        applied_at=1000.0,
        rollback_id="rrr-id",
    )
    defaults.update(kwargs)
    return IFOMTTLOverride(**defaults)


def test_override_valid_construction():
    o = _make_override()
    assert o.session_id == "sess-1"
    assert o.override_seconds == 3600.0
    assert o.rollback_status == "available"


def test_override_all_ttl_types():
    for ttl_type in ("active", "paused", "completed", "low_priority"):
        o = _make_override(ttl_type=ttl_type)
        assert o.ttl_type == ttl_type


def test_override_none_category_allowed():
    o = _make_override(category=None)
    assert o.category is None


def test_override_invalid_ttl_type_raises():
    with pytest.raises(ValueError, match="ttl_type"):
        _make_override(ttl_type="expired")  # type: ignore[arg-type]


def test_override_zero_seconds_raises():
    with pytest.raises(ValueError, match="override_seconds"):
        _make_override(override_seconds=0.0)


def test_override_negative_seconds_raises():
    with pytest.raises(ValueError, match="override_seconds"):
        _make_override(override_seconds=-1.0)


def test_override_previous_seconds_optional():
    o = _make_override(previous_seconds=2000.0)
    assert o.previous_seconds == 2000.0
    o2 = _make_override()
    assert o2.previous_seconds is None


# ---------------------------------------------------------------------------
# InMemoryIFOMTTLOverrideStore — CRUD
# ---------------------------------------------------------------------------


def test_store_read_missing_returns_none():
    store = InMemoryIFOMTTLOverrideStore()
    assert store.read_override("sess-1", "coding", "active") is None


def test_store_write_and_read():
    store = InMemoryIFOMTTLOverrideStore()
    override = _make_override()
    store.write_override(override)
    result = store.read_override("sess-1", "coding", "active")
    assert result is not None
    assert result.override_seconds == 3600.0


def test_store_write_overwrites_existing():
    store = InMemoryIFOMTTLOverrideStore()
    store.write_override(_make_override(override_seconds=3600.0))
    store.write_override(_make_override(override_seconds=7200.0))
    result = store.read_override("sess-1", "coding", "active")
    assert result.override_seconds == 7200.0


def test_store_delete_existing():
    store = InMemoryIFOMTTLOverrideStore()
    store.write_override(_make_override())
    store.delete_override("sess-1", "coding", "active")
    assert store.read_override("sess-1", "coding", "active") is None


def test_store_delete_nonexistent_no_error():
    store = InMemoryIFOMTTLOverrideStore()
    # Should not raise
    store.delete_override("sess-missing", "coding", "active")


def test_store_scoped_by_session():
    store = InMemoryIFOMTTLOverrideStore()
    store.write_override(_make_override(session_id="sess-1", override_seconds=3600.0))
    store.write_override(_make_override(session_id="sess-2", override_seconds=7200.0))
    assert store.read_override("sess-1", "coding", "active").override_seconds == 3600.0
    assert store.read_override("sess-2", "coding", "active").override_seconds == 7200.0


def test_store_scoped_by_category():
    store = InMemoryIFOMTTLOverrideStore()
    store.write_override(_make_override(category="coding", override_seconds=3600.0))
    store.write_override(_make_override(category="writing", override_seconds=7200.0))
    assert store.read_override("sess-1", "coding", "active").override_seconds == 3600.0
    assert store.read_override("sess-1", "writing", "active").override_seconds == 7200.0


def test_store_scoped_by_ttl_type():
    store = InMemoryIFOMTTLOverrideStore()
    store.write_override(_make_override(ttl_type="active", override_seconds=3600.0))
    store.write_override(_make_override(ttl_type="paused", override_seconds=1800.0))
    assert store.read_override("sess-1", "coding", "active").override_seconds == 3600.0
    assert store.read_override("sess-1", "coding", "paused").override_seconds == 1800.0


def test_store_none_category_scoped():
    store = InMemoryIFOMTTLOverrideStore()
    store.write_override(_make_override(category=None, override_seconds=3600.0))
    store.write_override(_make_override(category="coding", override_seconds=7200.0))
    assert store.read_override("sess-1", None, "active").override_seconds == 3600.0
    assert store.read_override("sess-1", "coding", "active").override_seconds == 7200.0


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def test_store_set_helper():
    store = InMemoryIFOMTTLOverrideStore()
    override = store.set("sess-1", "coding", "active", 5400.0)
    assert isinstance(override, IFOMTTLOverride)
    assert override.override_seconds == 5400.0
    assert store.read_override("sess-1", "coding", "active") is override


def test_store_snapshot():
    store = InMemoryIFOMTTLOverrideStore()
    store.set("sess-1", "coding", "active", 3600.0)
    store.set("sess-2", "writing", "paused", 1800.0)
    snap = store.snapshot()
    assert len(snap) == 2
    assert ("sess-1", "coding", "active") in snap


def test_store_initial_state():
    override = _make_override()
    store = InMemoryIFOMTTLOverrideStore(
        initial={("sess-1", "coding", "active"): override}
    )
    result = store.read_override("sess-1", "coding", "active")
    assert result is override

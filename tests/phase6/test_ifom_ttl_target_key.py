"""Phase 6 STEP 4 — IFOMTTLType target key builder/parser unit tests.

Tests build_ifom_ttl_target_key and parse_ifom_ttl_target_key.
"""
from __future__ import annotations

import pytest

from app.rpe.ifom_store import (
    _VALID_TTL_TYPES,
    build_ifom_ttl_target_key,
    parse_ifom_ttl_target_key,
)


# ---------------------------------------------------------------------------
# build_ifom_ttl_target_key
# ---------------------------------------------------------------------------


def test_build_with_category():
    assert build_ifom_ttl_target_key("active", "coding") == "active:coding"


def test_build_all_ttl_types_with_category():
    for ttl_type in ("active", "paused", "completed", "low_priority"):
        key = build_ifom_ttl_target_key(ttl_type, "coding")  # type: ignore[arg-type]
        assert key == f"{ttl_type}:coding"


def test_build_without_category_none():
    assert build_ifom_ttl_target_key("active", None) == "active:"


def test_build_without_category_empty_string():
    assert build_ifom_ttl_target_key("paused", "") == "paused:"


def test_build_paused_type():
    assert build_ifom_ttl_target_key("paused", "game_design") == "paused:game_design"


def test_build_completed_type():
    assert build_ifom_ttl_target_key("completed", "math_logic") == "completed:math_logic"


def test_build_low_priority_type():
    assert build_ifom_ttl_target_key("low_priority", "writing") == "low_priority:writing"


def test_build_category_with_colon_preserved():
    """Category containing ':' is allowed — parse will split at first ':'."""
    key = build_ifom_ttl_target_key("active", "system_design")
    assert key == "active:system_design"


# ---------------------------------------------------------------------------
# parse_ifom_ttl_target_key — valid inputs
# ---------------------------------------------------------------------------


def test_parse_with_category():
    ttl_type, category = parse_ifom_ttl_target_key("active:coding")
    assert ttl_type == "active"
    assert category == "coding"


def test_parse_without_category():
    ttl_type, category = parse_ifom_ttl_target_key("active:")
    assert ttl_type == "active"
    assert category is None


def test_parse_paused_with_category():
    ttl_type, category = parse_ifom_ttl_target_key("paused:game_design")
    assert ttl_type == "paused"
    assert category == "game_design"


def test_parse_completed():
    ttl_type, category = parse_ifom_ttl_target_key("completed:math_logic")
    assert ttl_type == "completed"
    assert category == "math_logic"


def test_parse_low_priority():
    ttl_type, category = parse_ifom_ttl_target_key("low_priority:writing")
    assert ttl_type == "low_priority"
    assert category == "writing"


def test_parse_roundtrip_all_types_with_category():
    """build + parse round-trips for all TTL types × a category."""
    for ttl_type in ("active", "paused", "completed", "low_priority"):
        key = build_ifom_ttl_target_key(ttl_type, "coding")  # type: ignore[arg-type]
        parsed_type, parsed_cat = parse_ifom_ttl_target_key(key)
        assert parsed_type == ttl_type
        assert parsed_cat == "coding"


def test_parse_roundtrip_none_category():
    for ttl_type in ("active", "paused", "completed", "low_priority"):
        key = build_ifom_ttl_target_key(ttl_type, None)  # type: ignore[arg-type]
        parsed_type, parsed_cat = parse_ifom_ttl_target_key(key)
        assert parsed_type == ttl_type
        assert parsed_cat is None


# ---------------------------------------------------------------------------
# parse_ifom_ttl_target_key — error cases
# ---------------------------------------------------------------------------


def test_parse_missing_colon_raises():
    with pytest.raises(ValueError, match="missing ':'"):
        parse_ifom_ttl_target_key("active")


def test_parse_unknown_ttl_type_raises():
    with pytest.raises(ValueError, match="Unknown IFOMTTLType"):
        parse_ifom_ttl_target_key("expired:coding")


def test_parse_empty_string_raises():
    with pytest.raises(ValueError):
        parse_ifom_ttl_target_key("")


def test_parse_only_colon_raises():
    with pytest.raises(ValueError, match="Unknown IFOMTTLType"):
        parse_ifom_ttl_target_key(":coding")


# ---------------------------------------------------------------------------
# Valid TTL type set
# ---------------------------------------------------------------------------


def test_valid_ttl_types_contains_four_values():
    assert _VALID_TTL_TYPES == frozenset({"active", "paused", "completed", "low_priority"})


def test_paused_is_valid_ttl_type():
    """Verify 'paused' is included (key STEP 4 requirement)."""
    assert "paused" in _VALID_TTL_TYPES

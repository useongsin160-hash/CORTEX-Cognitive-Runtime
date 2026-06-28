"""B10 — RPERecentCounter tests (read-side sign aggregation)."""
from __future__ import annotations

import pytest

from app.rpe.recent_counter import RPERecentCounter


def test_counts_positive_and_negative():
    c = RPERecentCounter()
    c.record("s", "coding", 0.06)
    c.record("s", "coding", 0.04)
    c.record("s", "coding", -0.02)
    assert c.counts("s", "coding") == (2, 1)


def test_unseen_cell_is_zero():
    assert RPERecentCounter().counts("s", "coding") == (0, 0)


def test_zero_delta_ignored():
    c = RPERecentCounter()
    c.record("s", "coding", 0.0)
    assert c.counts("s", "coding") == (0, 0)


def test_missing_session_or_category_noop():
    c = RPERecentCounter()
    c.record("", "coding", 0.06)
    c.record("s", "", 0.06)
    assert c.counts("s", "coding") == (0, 0)


def test_window_is_bounded():
    c = RPERecentCounter(window=3)
    for _ in range(5):
        c.record("s", "coding", 0.06)  # 5 positives, window 3
    assert c.counts("s", "coding") == (3, 0)  # only the last 3 kept


def test_cells_are_independent():
    c = RPERecentCounter()
    c.record("s", "coding", 0.06)
    c.record("s", "writing", -0.02)
    assert c.counts("s", "coding") == (1, 0)
    assert c.counts("s", "writing") == (0, 1)


def test_invalid_window_rejected():
    with pytest.raises(ValueError, match="window"):
        RPERecentCounter(window=0)

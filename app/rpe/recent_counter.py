"""RPE recent-outcome counter (B10).

A read-side tally of the *sign* of recent applied RPE mutations, keyed by
(session_id, category). It exists to fill the routing advisory's RPE term
(rpe_recent_positive_count / rpe_recent_negative_count), which had no production
source before — the advisory read 0/0 (B7). The signal is REAL (C1 now applies
mutations); this only counts the sign of each applied_delta, never fabricating.

In-memory, bounded (a deque of the last N signs per cell). Pure counting: it
never touches the mutation path, single-apply selection, or the gate — it is fed
post-apply and read by routes for the advisory pass.
"""
from __future__ import annotations

from collections import defaultdict, deque

_DEFAULT_WINDOW = 20


class RPERecentCounter:
    """Bounded per-(session, category) tally of recent applied-mutation signs."""

    def __init__(self, window: int = _DEFAULT_WINDOW) -> None:
        if window <= 0:
            raise ValueError(f"window must be > 0, got {window}")
        self._window = window
        # (session_id, category) -> deque of +1 / -1 (most recent last).
        self._signs: dict[tuple[str, str], deque[int]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )

    def record(self, session_id: str, category: str, applied_delta: float) -> None:
        """Record one applied mutation's sign. A zero delta is ignored (the
        mutation didn't actually move the weight). Missing session/category is a
        no-op (nothing to key on)."""
        if not session_id or not category or applied_delta == 0.0:
            return
        self._signs[(session_id, category)].append(1 if applied_delta > 0 else -1)

    def counts(self, session_id: str, category: str) -> tuple[int, int]:
        """Return (positive_count, negative_count) over the recent window for the
        cell. Unseen cell → (0, 0)."""
        signs = self._signs.get((session_id, category))
        if not signs:
            return 0, 0
        positive = sum(1 for s in signs if s > 0)
        return positive, len(signs) - positive

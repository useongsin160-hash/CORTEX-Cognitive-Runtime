"""Category×difficulty RPE learning store (B11 S1).

35-cell (7 categories × 5 difficulties) isolated learning substrate. **Additive**:
the existing category-only synapse_weight path (`mutators.SynapseWeightMutator` /
`InMemorySynapseWeightStore`) and the production `SynapseState` (7-cell, frozen)
are untouched — this is a separate backend keyed by (session_id, category,
difficulty). "쉬운 coding"과 "어려운 coding"이 별도 칸에서 독립 누적·분화한다.

target_key format: ``category:{category}:difficulty:{difficulty}`` (difficulty int,
B12 1~5). difficulty < 1 (RPEContext default 0 = unset) is rejected so an unset
difficulty never creates a learning cell — pollution guard (확정 결정).

Weight bounds [0.1, 1.0] are inherited from the existing path; this module never
raises the 1.0 ceiling and adds no per-difficulty cap (emergent invariant — the
난이도별 비중 밴드는 보장 구조가 아니라 학습 결과로 수용).
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Protocol, runtime_checkable

_TARGET_PREFIX = "category:"
_DIFF_SEP = ":difficulty:"

# Bounded LRU over (session, category, difficulty) cells so the in-memory store
# never grows unbounded on the 8GB host. ~512 sessions × 35 cells (mirrors
# routing_ratchet's MAX_SESSIONS budget). An evicted cell reads back as None
# (truly unlearned → no routing override) — the same graceful degradation as decay.
MAX_CELLS: int = 512 * 35


def build_cat_diff_target_key(category: str, difficulty: int) -> str:
    """Build ``category:{category}:difficulty:{difficulty}``.

    Raises ValueError on an empty category or difficulty < 1 (unset/0 must never
    create a cell).
    """
    if not category:
        raise ValueError("category must be a non-empty string")
    if difficulty < 1:
        raise ValueError(
            f"difficulty must be >= 1 for a cat×diff key, got {difficulty} "
            "(unset/0 difficulty must not create a learning cell)"
        )
    return f"{_TARGET_PREFIX}{category}{_DIFF_SEP}{difficulty}"


def parse_cat_diff_target_key(target_key: str) -> tuple[str, int]:
    """Parse ``category:{cat}:difficulty:{d}`` → (category, difficulty).

    Raises ValueError on a malformed key, a non-int difficulty, or difficulty < 1.
    """
    if not target_key.startswith(_TARGET_PREFIX) or _DIFF_SEP not in target_key:
        raise ValueError(
            f"target_key must be 'category:{{cat}}:difficulty:{{d}}', got {target_key!r}"
        )
    body = target_key[len(_TARGET_PREFIX):]
    category, _, diff_part = body.partition(_DIFF_SEP)
    if not category:
        raise ValueError(f"empty category in target_key {target_key!r}")
    try:
        difficulty = int(diff_part)
    except ValueError:
        raise ValueError(
            f"difficulty must be an int in target_key {target_key!r}, got {diff_part!r}"
        ) from None
    if difficulty < 1:
        raise ValueError(
            f"difficulty must be >= 1 in target_key {target_key!r}, got {difficulty}"
        )
    return category, difficulty


@runtime_checkable
class SynapseDifficultyWeightStoreProtocol(Protocol):
    async def read_weight(
        self, session_id: str, category: str, difficulty: int
    ) -> float | None: ...

    async def write_weight(
        self, session_id: str, category: str, difficulty: int, value: float
    ) -> None: ...


class InMemorySynapseDifficultyWeightStore:
    """Deterministic in-memory 35-cell store keyed (session, category, difficulty).

    Separate from production SynapseState — never wraps or mutates it. Missing
    keys read as None (the caller seeds the initial weight).
    """

    def __init__(
        self,
        initial: dict[tuple[str, str, int], float] | None = None,
        max_cells: int = MAX_CELLS,
    ) -> None:
        # OrderedDict = LRU over cells; most-recently-touched at the end.
        self._weights: "OrderedDict[tuple[str, str, int], float]" = OrderedDict(
            initial or {}
        )
        self._max_cells = max_cells

    async def read_weight(
        self, session_id: str, category: str, difficulty: int
    ) -> float | None:
        key = (session_id, category, difficulty)
        if key not in self._weights:
            return None
        self._weights.move_to_end(key)  # LRU touch on read
        return self._weights[key]

    async def write_weight(
        self, session_id: str, category: str, difficulty: int, value: float
    ) -> None:
        self._put((session_id, category, difficulty), value)

    def _put(self, key: tuple[str, str, int], value: float) -> None:
        self._weights[key] = value
        self._weights.move_to_end(key)
        if len(self._weights) > self._max_cells:
            self._weights.popitem(last=False)  # evict least-recently-used cell

    # ----- test / inspection helpers -----

    def snapshot(self) -> dict[tuple[str, str, int], float]:
        return dict(self._weights)

    def set(
        self, session_id: str, category: str, difficulty: int, value: float
    ) -> None:
        self._put((session_id, category, difficulty), value)

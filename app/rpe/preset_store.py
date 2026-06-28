"""Global category×difficulty EMA preset + presetted difficulty store (B3b).

C-hybrid persistence for the 35-cell difficulty learning:

- Per-session live learning stays in-memory (B11 session dynamics — ratchet,
  decay, biological routing — are untouched). `PresettedDifficultyStore` is a
  drop-in for `InMemorySynapseDifficultyWeightStore`: session weights live in a
  plain dict; writes (from the mutator AND from decay) go to that dict only — no
  DB, no EMA.
- A global ``(category, difficulty)`` preset (35 rows) is persisted to aiosqlite
  so learning survives restart. It is updated by an EMA roll-up that fires ONLY
  after a learning mutation (driven by the difficulty service post-apply), NEVER
  by decay — so decay erodes the in-memory session weight but never reaches the
  global preset. On startup the preset is loaded and becomes the read-fallback,
  so a new session begins from the learned value instead of the 0.3 seed.

read_weight resolves: session value → global preset → None. ``None`` (truly
unlearned, no preset) still means "no override" for the routing layer; a preset
value is a learned start (distinct from None), which is exactly the intent.

Isolation: imports only aiosqlite + app.core.errors + app.db.sqlite. No app.api /
app.main / app.routing / app.memory / RPE-core logic / network / LLM.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone

import aiosqlite

from app.core.errors import DatabaseError
from app.db.sqlite import _normalize_path
from app.rpe.difficulty_store import MAX_CELLS

# Inherited emergent bounds — the preset never stores a weight outside these.
_WEIGHT_MIN = 0.1
_WEIGHT_MAX = 1.0
# EMA smoothing: new = α·session_value + (1−α)·prev. Recency over permanence
# (consistent with decay's "use it or lose it"). Start value; tune after B6.
_DEFAULT_ALPHA = 0.3

_DDL = """
CREATE TABLE IF NOT EXISTS rpe_difficulty_weights (
    category    TEXT NOT NULL,
    difficulty  INTEGER NOT NULL,
    weight      REAL NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (category, difficulty)
)
"""


def _clamp(value: float) -> float:
    return max(_WEIGHT_MIN, min(_WEIGHT_MAX, value))


class DifficultyPresetStore:
    """aiosqlite-backed global (category, difficulty) EMA preset (35 rows).

    update_ema is called ONLY by learning mutations (not decay). get_preset reads
    the in-memory cache (no DB on the read path); load_all fills the cache at
    startup.
    """

    def __init__(self, database_url: str, alpha: float = _DEFAULT_ALPHA) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha must be in (0.0, 1.0], got {alpha}")
        self._path = _normalize_path(database_url)
        self._alpha = alpha
        self._init_lock = asyncio.Lock()
        self._initialized = False
        # (category, difficulty) → weight. Source of truth for reads; mirrors DB.
        self._cache: dict[tuple[str, int], float] = {}

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                async with aiosqlite.connect(self._path) as conn:
                    await conn.execute(_DDL)
                    await conn.commit()
            except sqlite3.Error as exc:
                raise DatabaseError(f"DifficultyPresetStore init failed: {exc}") from exc
            self._initialized = True

    async def update_ema(self, category: str, difficulty: int, value: float) -> None:
        """EMA-roll a learning mutation's resulting weight into the global preset.

        Cache is updated first (live read correctness), then persisted (durability;
        a DB error leaves the cache ahead — the caller logs fail-open).
        """
        await self._ensure_init()
        prev = self._cache.get((category, difficulty))
        new = value if prev is None else self._alpha * value + (1.0 - self._alpha) * prev
        new = _clamp(new)
        self._cache[(category, difficulty)] = new
        updated_at = datetime.now(timezone.utc).isoformat()
        try:
            async with aiosqlite.connect(self._path) as conn:
                await conn.execute(
                    "INSERT INTO rpe_difficulty_weights "
                    "(category, difficulty, weight, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(category, difficulty) DO UPDATE SET "
                    "weight = excluded.weight, updated_at = excluded.updated_at",
                    (category, difficulty, new, updated_at),
                )
                await conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"DifficultyPresetStore update_ema failed: {exc}"
            ) from exc

    async def load_all(self) -> None:
        """Load every persisted preset row into the cache (startup). Clamps to
        the emergent bounds defensively (a hand-edited/legacy row never escapes)."""
        await self._ensure_init()
        try:
            async with aiosqlite.connect(self._path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT category, difficulty, weight FROM rpe_difficulty_weights"
                ) as cur:
                    rows = await cur.fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"DifficultyPresetStore load_all failed: {exc}") from exc
        self._cache = {
            (r["category"], int(r["difficulty"])): _clamp(float(r["weight"]))
            for r in rows
        }

    def get_preset(self, category: str, difficulty: int) -> float | None:
        """Synchronous cache read — the routing/read path pays no DB cost."""
        return self._cache.get((category, difficulty))

    def snapshot(self) -> dict[tuple[str, int], float]:
        return dict(self._cache)


class PresettedDifficultyStore:
    """SynapseDifficultyWeightStoreProtocol impl: per-session in-memory weights
    over a global preset read-fallback.

    Drop-in for InMemorySynapseDifficultyWeightStore. write_weight goes to the
    session dict ONLY (decay + mutator both write here — no DB, no EMA). The
    learning EMA roll-up to the preset is the difficulty service's job, so the
    global preset never sees decay.
    """

    def __init__(
        self,
        preset: DifficultyPresetStore,
        initial: dict[tuple[str, str, int], float] | None = None,
        max_cells: int = MAX_CELLS,
    ) -> None:
        # OrderedDict = bounded LRU over session cells (8GB host). An evicted cell
        # gracefully falls back to the global preset on read (a learned start), so
        # eviction never produces a worse signal than the persisted preset.
        self._weights: "OrderedDict[tuple[str, str, int], float]" = OrderedDict(
            initial or {}
        )
        self._preset = preset
        self._max_cells = max_cells

    async def read_weight(
        self, session_id: str, category: str, difficulty: int
    ) -> float | None:
        key = (session_id, category, difficulty)
        value = self._weights.get(key)
        if value is not None:
            self._weights.move_to_end(key)  # LRU touch on read
            return value
        # Fallback: a global preset value is a LEARNED start (distinct from None =
        # truly unlearned → no routing override).
        return self._preset.get_preset(category, difficulty)

    async def write_weight(
        self, session_id: str, category: str, difficulty: int, value: float
    ) -> None:
        self._put((session_id, category, difficulty), value)

    def _put(self, key: tuple[str, str, int], value: float) -> None:
        self._weights[key] = value
        self._weights.move_to_end(key)
        if len(self._weights) > self._max_cells:
            self._weights.popitem(last=False)  # evict least-recently-used cell

    # ----- test / inspection helpers (mirror InMemory) -----

    def snapshot(self) -> dict[tuple[str, str, int], float]:
        return dict(self._weights)

    def set(
        self, session_id: str, category: str, difficulty: int, value: float
    ) -> None:
        self._put((session_id, category, difficulty), value)

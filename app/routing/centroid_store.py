"""Phase 3 — vector-cluster centroid lookup.

Centroid-based classification: each of the 7 categories is represented
by the **mean-centered** mean of its seed embeddings; classification
picks the category whose centroid maximizes cosine similarity. Same
embedder as SemanticCache (multilingual-e5-base) so the vector space
is shared end-to-end.

bilingual_average 전략 (v2.0):
각 시드를 영문+한국어 페어로 임베딩 후 평균.
centroid가 양쪽 언어 의미 공간의 중간에 정렬되어,
영문 쿼리와 한국어 쿼리 모두에 대해 매치율 확보.

mean-centering (v2.1, e5 호환):
multilingual-e5-base의 임베딩은 토픽 무관하게 한 방향으로 강하게
편향되어 raw centroid의 쌍별 코사인이 0.95+에 고정된다. 이를 풀기
위해 모든 시드 임베딩의 전역 평균을 빼고(=공통 방향 제거) 카테고리별
평균을 계산한다. 쿼리 시점에도 동일한 전역 평균을 빼서 같은 좌표계
에서 코사인 비교가 일어나도록 한다. 전역 평균은 cache npz에 함께
저장되어 재현성을 보장한다.

설계 철학: "한 글자의 오차도 허용 안 함" → 언어별 매치율 불균형 방지.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

import numpy as np

from app.core.embedder import get_embedding_function

CACHE_VERSION = "v2.1_e5_bilingual"
_VERSION_KEY = "__schema_version__"
_GLOBAL_MEAN_KEY = "__global_mean__"

Strategy = Literal["bilingual", "en_only", "ko_only"]


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


class CentroidStore:
    """Computes / caches / queries per-category centroids."""

    def __init__(self, cache_path: Path | str | None = None) -> None:
        self._embedder = get_embedding_function()
        self._centroids: dict[str, np.ndarray] = {}
        self._global_mean: np.ndarray | None = None
        self._cache_path = Path(cache_path) if cache_path else Path("data/centroids.npz")
        self.last_load_was_cached: bool = False

    @property
    def categories(self) -> list[str]:
        return list(self._centroids.keys())

    @property
    def global_mean(self) -> np.ndarray | None:
        """Exposed for diagnostics. Built from all per-seed embeddings."""
        return self._global_mean

    def _embed_sync(self, texts: list[str]) -> np.ndarray:
        vectors = self._embedder(texts)
        return np.asarray(vectors, dtype=np.float32)

    async def _embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self._embed_sync, texts)

    async def build_from_seeds(
        self,
        seed_path: Path | str,
        strategy: Strategy = "bilingual",
    ) -> None:
        """Compute centroids from seed JSON.

        Strategies (v2.0+):
          - "bilingual": for each seed, embed `en` and `ko` separately,
            average them, then average across seeds → cross-lingual aligned.
          - "en_only" / "ko_only": single-language diagnostics, no cache.

        v2.1 adds mean-centering: a global mean across ALL per-seed
        embeddings is subtracted before per-category averaging. The same
        global mean is later subtracted from query embeddings in
        find_nearest, so queries and centroids share a coordinate system.

        Only the "bilingual" strategy reads / writes data/centroids.npz.
        Cache invalidates on mtime mismatch OR CACHE_VERSION mismatch.
        """
        seed = Path(seed_path)
        seed_mtime = seed.stat().st_mtime
        cacheable = strategy == "bilingual"

        if cacheable and self._cache_path.exists():
            try:
                data = np.load(self._cache_path, allow_pickle=False)
                cache_mtime = self._cache_path.stat().st_mtime
                version_ok = (
                    _VERSION_KEY in data.files
                    and str(data[_VERSION_KEY].item()) == CACHE_VERSION
                )
                has_mean = _GLOBAL_MEAN_KEY in data.files
                if version_ok and has_mean and cache_mtime >= seed_mtime:
                    self._centroids = {
                        key: np.asarray(data[key], dtype=np.float32)
                        for key in data.files
                        if key not in (_VERSION_KEY, _GLOBAL_MEAN_KEY)
                    }
                    self._global_mean = np.asarray(
                        data[_GLOBAL_MEAN_KEY], dtype=np.float32
                    )
                    self.last_load_was_cached = True
                    return
            except (OSError, ValueError, KeyError):
                pass  # stale / corrupt — fall through to fresh build

        payload = json.loads(seed.read_text(encoding="utf-8"))
        categories = payload["categories"]

        per_category_seeds: dict[str, np.ndarray] = {}
        for category, items in categories.items():
            if not items:
                continue
            if strategy == "en_only":
                per_category_seeds[category] = await self._embed(
                    [item["en"] for item in items]
                )
            elif strategy == "ko_only":
                per_category_seeds[category] = await self._embed(
                    [item["ko"] for item in items]
                )
            else:  # bilingual
                en_emb = await self._embed([item["en"] for item in items])
                ko_emb = await self._embed([item["ko"] for item in items])
                per_category_seeds[category] = (en_emb + ko_emb) / 2.0

        all_seeds = np.vstack(list(per_category_seeds.values()))
        global_mean = all_seeds.mean(axis=0).astype(np.float32)

        computed: dict[str, np.ndarray] = {}
        for category, per_seed in per_category_seeds.items():
            centered = per_seed - global_mean
            centroid = centered.mean(axis=0)
            computed[category] = _normalize(centroid).astype(np.float32)

        self._centroids = computed
        self._global_mean = global_mean
        self.last_load_was_cached = False

        if cacheable:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload_to_save: dict[str, np.ndarray] = dict(computed)
            payload_to_save[_VERSION_KEY] = np.array(CACHE_VERSION, dtype="U32")
            payload_to_save[_GLOBAL_MEAN_KEY] = global_mean
            np.savez(self._cache_path, **payload_to_save)

    async def get_centroid(self, category: str) -> np.ndarray:
        if not self._centroids:
            raise RuntimeError("CentroidStore not initialized; call build_from_seeds() first")
        if category not in self._centroids:
            raise KeyError(f"Unknown category: {category}")
        return self._centroids[category]

    async def find_nearest(
        self,
        embedding: np.ndarray,
        clamp: bool = True,
    ) -> tuple[str, float]:
        """Return (category, cosine_similarity).

        Contract: similarity, not distance. 1.0 = identical direction.
        The query is mean-centered with the same global mean used at
        build time, then unit-normalized, so it sits in the centroids'
        coordinate frame.

        When `clamp=True` (default) the similarity is constrained to
        [0, 1] for the original Phase 3 STEP 1 contract. Callers that
        need the raw signed cosine (Phase 3 STEP 2 SemanticEvaluator
        reporting "음수 발생 여부") should pass `clamp=False` to receive
        the theoretical [-1, 1] value.
        """
        if not self._centroids:
            raise RuntimeError("CentroidStore not initialized; call build_from_seeds() first")
        raw = np.asarray(embedding, dtype=np.float32)
        if self._global_mean is not None:
            raw = raw - self._global_mean
        query = _normalize(raw)

        best_category = ""
        best_similarity = -1.0
        for category, centroid in self._centroids.items():
            similarity = float(np.dot(query, centroid))
            if similarity > best_similarity:
                best_similarity = similarity
                best_category = category

        if clamp:
            best_similarity = max(0.0, min(1.0, best_similarity))
        else:
            best_similarity = max(-1.0, min(1.0, best_similarity))
        return best_category, best_similarity

    async def embed_text(self, text: str) -> np.ndarray:
        """Single-prompt embedding using the same embedder as the centroids.

        Returns the **raw** (uncentered) embedding so callers can pass it
        to find_nearest which applies centering internally.
        """
        embeddings = await self._embed([text])
        return embeddings[0]

    async def separation_matrix(self) -> dict[tuple[str, str], float]:
        """Pairwise cosine similarity between centroids — diagnostics tool.

        Centroids are stored in the centered/normalized frame, so this is
        the post-centering separation (the production metric)."""
        result: dict[tuple[str, str], float] = {}
        names = list(self._centroids.keys())
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                sim = float(np.dot(self._centroids[a], self._centroids[b]))
                result[(a, b)] = sim
        return result

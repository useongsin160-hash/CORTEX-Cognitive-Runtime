"""Tests for app/routing/centroid_store.py (v2.1 mean-centered, e5).

Embedder: intfloat/multilingual-e5-base (pre-cached, HF_HUB_OFFLINE=1).
Strategy: bilingual_average + global-mean centering. Tests exercise the
real embedder so changes to the embedder/centering pipeline surface here.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.routing.centroid_store import CACHE_VERSION, CentroidStore

SEED_PATH = Path(__file__).parent / "seed_queries.json"

SEPARATION_MAX = 0.85         # spec absolute gate
SEPARATION_TARGET = 0.5       # spec "가능하면 <" target
DELTA_MAX = 0.15              # spec |bilingual - en_only| limit

EXPECTED_CATEGORIES = {
    "coding",
    "game_design",
    "math_logic",
    "writing",
    "data_analysis",
    "system_design",
    "general",
}

# Out-of-corpus probes that exercise cross-lingual consistency. Each pair
# expresses the same intent in English and Korean; both must classify to
# the same expected category.
CROSS_LINGUAL_PROBES: list[tuple[str, str, str]] = [
    ("help me debug this python script", "이 파이썬 스크립트 디버깅 좀 해줘", "coding"),
    ("polish this paragraph of my essay", "이 에세이 문단 좀 다듬어줘", "writing"),
    ("prove this theorem step by step", "이 정리를 단계별로 증명해줘", "math_logic"),
    ("design a payments architecture", "결제 시스템 아키텍처 설계해줘", "system_design"),
    ("balance the boss fight reward design", "퀘스트 보상 밸런싱", "game_design"),
    ("plot monthly sales trend chart", "월별 매출 추세 차트 시각화", "data_analysis"),
    ("recommend a movie to watch tonight", "오늘 저녁에 볼 영화 추천", "general"),
]


@pytest.fixture(scope="module")
def cache_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("centroid_cache")


@pytest.fixture(scope="module")
async def built_store(cache_dir) -> CentroidStore:
    store = CentroidStore(cache_path=cache_dir / "centroids.npz")
    await store.build_from_seeds(SEED_PATH)
    return store


# ── v2.0 paired schema ----------------------------------------------------
def test_seeds_have_paired_en_ko_fields():
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    assert payload["metadata"]["version"] == "2.0"
    assert payload["metadata"]["embedding_strategy"] == "bilingual_average"
    categories = payload["categories"]
    assert set(categories.keys()) == EXPECTED_CATEGORIES

    total = 0
    canonical_count = 0
    edge_case_count = 0
    for cat, items in categories.items():
        assert len(items) == 10, f"{cat} should have 10 seeds, has {len(items)}"
        for item in items:
            assert "en" in item and item["en"], f"{item.get('id')} missing en"
            assert "ko" in item and item["ko"], f"{item.get('id')} missing ko"
            assert item["type"] in ("canonical", "edge_case")
            total += 1
            if item["type"] == "canonical":
                canonical_count += 1
            else:
                edge_case_count += 1
    assert total == 70
    assert canonical_count == 49
    assert edge_case_count == 21


# ── basic centroid build --------------------------------------------------
@pytest.mark.asyncio
async def test_build_from_seeds_creates_seven_centroids(built_store: CentroidStore):
    assert set(built_store.categories) == EXPECTED_CATEGORIES
    assert built_store.global_mean is not None
    for category in EXPECTED_CATEGORIES:
        vec = await built_store.get_centroid(category)
        assert isinstance(vec, np.ndarray)
        assert vec.ndim == 1
        assert vec.shape[0] > 0
        assert pytest.approx(float(np.linalg.norm(vec)), abs=1e-4) == 1.0


@pytest.mark.asyncio
async def test_seed_text_classifies_to_own_category(built_store: CentroidStore):
    """Smoke: a verbatim seed text must classify to its source category
    after centering + normalization. This is the round-trip check that
    replaces the legacy 'centroid-as-query → similarity 1.0' assertion
    (which no longer holds once a global mean is subtracted)."""
    seed_payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    # Pick one canonical English text and one canonical Korean text per
    # category and assert both classify to that category.
    for category, items in seed_payload["categories"].items():
        canonicals = [i for i in items if i["type"] == "canonical"]
        first = canonicals[0]
        for lang_key in ("en", "ko"):
            emb = await built_store.embed_text(first[lang_key])
            predicted, similarity = await built_store.find_nearest(emb)
            assert predicted == category, (
                f"{category}/{lang_key}: seed text '{first[lang_key]}' "
                f"classified as {predicted}"
            )
            assert 0.0 <= similarity <= 1.0


@pytest.mark.asyncio
async def test_find_nearest_similarity_is_in_0_1(built_store: CentroidStore):
    rng = np.random.default_rng(seed=42)
    centroid = await built_store.get_centroid("coding")
    for _ in range(20):
        random_vec = rng.normal(size=centroid.shape).astype(np.float32)
        _, similarity = await built_store.find_nearest(random_vec)
        assert 0.0 <= similarity <= 1.0


@pytest.mark.asyncio
async def test_find_nearest_returns_similarity_not_distance(built_store: CentroidStore):
    """Embedding a canonical coding seed text and querying it must yield
    a positive similarity for the matched category — confirms the value
    returned is a similarity (high = closer) not a distance."""
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    coding_seed_text = seed["categories"]["coding"][0]["en"]
    emb = await built_store.embed_text(coding_seed_text)
    category, similarity = await built_store.find_nearest(emb)
    assert category == "coding"
    assert similarity > 0.0, (
        f"got {similarity} — looks like distance leaked through "
        "instead of similarity"
    )


# ── caching (now also covers global_mean) --------------------------------
@pytest.mark.asyncio
async def test_second_build_uses_cache(cache_dir):
    store1 = CentroidStore(cache_path=cache_dir / "second_run.npz")
    await store1.build_from_seeds(SEED_PATH)
    assert store1.last_load_was_cached is False
    assert store1.global_mean is not None

    store2 = CentroidStore(cache_path=cache_dir / "second_run.npz")
    await store2.build_from_seeds(SEED_PATH)
    assert store2.last_load_was_cached is True
    for category in store1.categories:
        v1 = await store1.get_centroid(category)
        v2 = await store2.get_centroid(category)
        assert np.allclose(v1, v2, atol=1e-5)
    # global mean must round-trip too — otherwise queries would be
    # centered against the wrong frame after a cache load.
    assert np.allclose(store1.global_mean, store2.global_mean, atol=1e-5)


@pytest.mark.asyncio
async def test_cache_carries_version_and_global_mean(cache_dir):
    cache_path = cache_dir / "versioned.npz"
    store = CentroidStore(cache_path=cache_path)
    await store.build_from_seeds(SEED_PATH)
    data = np.load(cache_path, allow_pickle=False)
    assert "__schema_version__" in data.files
    assert str(data["__schema_version__"].item()) == CACHE_VERSION
    assert "__global_mean__" in data.files


# ── separation (production gate) ----------------------------------------
@pytest.mark.asyncio
async def test_category_separation_below_threshold(built_store: CentroidStore):
    matrix = await built_store.separation_matrix()
    offenders = {pair: sim for pair, sim in matrix.items() if sim >= SEPARATION_MAX}
    assert not offenders, (
        f"category centroids too close (>={SEPARATION_MAX}): {offenders}"
    )


@pytest.mark.asyncio
async def test_category_separation_meets_target(built_store: CentroidStore):
    """Stretch goal from the v2.0 spec: under e5 + mean-centering, every
    pair should fall below 0.5 (not just below the 0.85 absolute gate)."""
    matrix = await built_store.separation_matrix()
    above_target = {pair: sim for pair, sim in matrix.items() if sim >= SEPARATION_TARGET}
    assert not above_target, (
        f"pairs above target {SEPARATION_TARGET}: {above_target}"
    )


@pytest.mark.asyncio
async def test_bilingual_separation_within_delta_of_en_only(cache_dir):
    """Spec gate: bilingual worst-pair separation must stay within 0.15
    of en_only — i.e., the bilingual average shouldn't crater the
    English-only separation we're inheriting."""
    en_store = CentroidStore(cache_path=cache_dir / "en_sep.npz")
    bi_store = CentroidStore(cache_path=cache_dir / "bi_sep.npz")
    await en_store.build_from_seeds(SEED_PATH, strategy="en_only")
    await bi_store.build_from_seeds(SEED_PATH, strategy="bilingual")

    en_worst = max((await en_store.separation_matrix()).values())
    bi_worst = max((await bi_store.separation_matrix()).values())
    delta = bi_worst - en_worst
    assert abs(delta) <= DELTA_MAX, (
        f"bilingual worst={bi_worst:.4f} drifted from en_only worst="
        f"{en_worst:.4f} by Δ={delta:+.4f} (limit ±{DELTA_MAX})"
    )


# ── cross-lingual consistency (HEADLINE acceptance gate) ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "en_query,ko_query,expected", CROSS_LINGUAL_PROBES,
    ids=[probe[2] for probe in CROSS_LINGUAL_PROBES],
)
async def test_cross_lingual_query_lands_in_same_category(
    built_store: CentroidStore, en_query: str, ko_query: str, expected: str,
):
    en_emb = await built_store.embed_text(en_query)
    ko_emb = await built_store.embed_text(ko_query)
    en_cat, _ = await built_store.find_nearest(en_emb)
    ko_cat, _ = await built_store.find_nearest(ko_emb)
    assert en_cat == expected, f"en '{en_query}' got {en_cat}, expected {expected}"
    assert ko_cat == expected, f"ko '{ko_query}' got {ko_cat}, expected {expected}"


# ── error contracts -----------------------------------------------------
@pytest.mark.asyncio
async def test_get_centroid_raises_for_unknown_category(built_store: CentroidStore):
    with pytest.raises(KeyError):
        await built_store.get_centroid("not_a_real_category")


@pytest.mark.asyncio
async def test_find_nearest_before_build_raises(tmp_path):
    fresh = CentroidStore(cache_path=tmp_path / "empty.npz")
    with pytest.raises(RuntimeError):
        await fresh.find_nearest(np.zeros(768, dtype=np.float32))

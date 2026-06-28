import aiosqlite
import pytest

from app.ingress.cache_key import exact_key
from app.ingress.exact_cache import ExactCache
from app.ingress.semantic_cache import SemanticCache


# ---- ExactCache ----------------------------------------------------------

@pytest.fixture
def exact_cache(tmp_path):
    return ExactCache(str(tmp_path / "exact.db"))


@pytest.mark.asyncio
async def test_exact_put_then_get_hits(exact_cache):
    await exact_cache.put("hello world", "world reply")
    assert await exact_cache.get("hello world") == "world reply"


@pytest.mark.asyncio
async def test_exact_unknown_prompt_misses(exact_cache):
    await exact_cache.put("seeded prompt", "seeded reply")
    assert await exact_cache.get("entirely different prompt") is None


@pytest.mark.asyncio
async def test_exact_hit_count_increments(tmp_path):
    db_path = tmp_path / "exact.db"
    cache = ExactCache(str(db_path))
    await cache.put("counted", "reply")
    for _ in range(3):
        assert await cache.get("counted") == "reply"

    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute(
            "SELECT hit_count FROM exact_cache WHERE prompt_hash = ?",
            # put() 은 기본 llm_mode="mock" (slot/model unresolved) 네임스페이스로
            # 저장하므로 동일 네임스페이스 키로 조회한다.
            (exact_key("counted", llm_mode="mock"),),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 3


# ---- SemanticCache -------------------------------------------------------

# Skip the whole semantic suite if chromadb's default embedder cannot init
# (e.g. no internet for the model download in this sandbox).
chromadb = pytest.importorskip("chromadb")


@pytest.fixture
def semantic_cache(make_ephemeral_cache):
    # 실 multilingual-e5-base 임베더(공유 싱글톤) + EphemeralClient(독립 in-memory system).
    # PersistentClient teardown churn / ./data 오염 / Windows 파일 락 없이 실 임베딩 거리
    # 동작을 검증한다. teardown 은 make_ephemeral_cache 가 close() 한다.
    return make_ephemeral_cache(real=True)


# Thresholds calibrated for the multilingual-e5-base embedder swapped in
# at Phase 3 STEP 1.5. e5 maps any-vs-any sentences into the 0.75-0.98
# band (vs the old MiniLM 0.3-0.95 range), so the cache threshold floor
# moves up to 0.90 to keep "similar" hits in and "unrelated" hits out.
@pytest.mark.asyncio
async def test_semantic_similar_prompt_hits(semantic_cache):
    await semantic_cache.put(
        "How do I sort a list in Python?",
        "Use the sorted() builtin or list.sort().",
    )
    result = await semantic_cache.get(
        "What's the way to sort a python list?",
        threshold=0.90,
    )
    assert result is not None
    response, similarity = result
    assert "sorted" in response
    assert similarity >= 0.90


@pytest.mark.asyncio
async def test_semantic_unrelated_prompt_misses(semantic_cache):
    await semantic_cache.put(
        "Recipe for chocolate cake",
        "Mix flour, sugar, cocoa, eggs; bake at 180C.",
    )
    result = await semantic_cache.get(
        "Quantum entanglement and Bell's theorem",
        threshold=0.90,
    )
    assert result is None


@pytest.mark.asyncio
async def test_semantic_threshold_too_high_misses(semantic_cache):
    await semantic_cache.put(
        "How do I sort a list in Python?",
        "Use sorted().",
    )
    # Even an exact-ish match should not clear an absurdly high threshold.
    result = await semantic_cache.get(
        "completely unrelated question about the weather on mars",
        threshold=0.99,
    )
    assert result is None

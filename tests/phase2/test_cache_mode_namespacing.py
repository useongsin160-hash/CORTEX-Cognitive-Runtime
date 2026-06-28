"""OVERTURE A1 — 영속 캐시 mode/slot 네임스페이스 격리 회귀.

영속 캐시(ExactCache=SQLite, SemanticCache=ChromaDB)가 llm_mode/슬롯 식별자를
키에 담지 않아 live 가 mock 시대 답변을 hit 하던 정직성 버그를 막는 read-side
hardening 의 회귀 스위트다.

SemanticCache 검증은 **실제 multilingual-e5-base 임베더를 로드하지 않는다**:
결정론적 fake embedding function + ephemeral collection 으로 네임스페이스 격리만
검증한다(격리는 임베딩 품질과 무관하며, e5 반복 로드는 Windows 네이티브 크래시
위험이 있다 — acceptance condition 7).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from app.ingress.cache_key import (
    CachePolicyError,
    exact_key,
    slot_fingerprint,
)
from app.ingress.exact_cache import ExactCache, _hash

# ── 슬롯 픽스처(public-safe fingerprint) ────────────────────────────────────
# SLOT_A/SLOT_B: 같은 model 문자열, 다른 base_url → fingerprint 만 다름
# (= "같은 모델, 다른 슬롯" 격리 검증). SLOT_C: 모든 게 다른 슬롯.
_SLOT_A = {
    "slot_fingerprint": slot_fingerprint(
        tier_name="STANDARD", protocol="google",
        base_url="https://a.example", model="gemini-flash-lite",
    ),
    "model_id": "gemini-flash-lite",
}
_SLOT_B = {
    "slot_fingerprint": slot_fingerprint(
        tier_name="STANDARD", protocol="google",
        base_url="https://b.example", model="gemini-flash-lite",
    ),
    "model_id": "gemini-flash-lite",
}
_SLOT_C = {
    "slot_fingerprint": slot_fingerprint(
        tier_name="DEEP_THINKING", protocol="google",
        base_url="https://c.example", model="gemini-pro",
    ),
    "model_id": "gemini-pro",
}


# ═══════════════════════════════════════════════════════════════════════════
# ExactCache (SQLite — 경량, e5 무관)
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture
def exact(tmp_path):
    return ExactCache(str(tmp_path / "exact.db"))


@pytest.mark.asyncio
async def test_exact_mock_put_mock_get_hits(exact):
    await exact.put("p", "mock-reply", llm_mode="mock")
    assert await exact.get("p", llm_mode="mock") == "mock-reply"


@pytest.mark.asyncio
async def test_exact_live_slot_put_get_hits(exact):
    await exact.put("p", "live-A-reply", llm_mode="live", **_SLOT_A)
    assert await exact.get("p", llm_mode="live", **_SLOT_A) == "live-A-reply"


@pytest.mark.asyncio
async def test_exact_mock_live_cross_miss_both_directions(exact):
    # mock put → live get → miss
    await exact.put("p", "mock-reply", llm_mode="mock")
    assert await exact.get("p", llm_mode="live", **_SLOT_A) is None
    # live put → mock get → miss
    await exact.put("q", "live-reply", llm_mode="live", **_SLOT_A)
    assert await exact.get("q", llm_mode="mock") is None


@pytest.mark.asyncio
async def test_exact_live_slotA_slotB_cross_miss(exact):
    await exact.put("p", "live-A", llm_mode="live", **_SLOT_A)
    assert await exact.get("p", llm_mode="live", **_SLOT_C) is None


@pytest.mark.asyncio
async def test_exact_same_model_different_slot_cross_miss(exact):
    # 같은 model_id, 다른 slot_fingerprint → 교차 miss.
    assert _SLOT_A["slot_fingerprint"] != _SLOT_B["slot_fingerprint"]
    assert _SLOT_A["model_id"] == _SLOT_B["model_id"]
    await exact.put("p", "live-A", llm_mode="live", **_SLOT_A)
    assert await exact.get("p", llm_mode="live", **_SLOT_B) is None


@pytest.mark.asyncio
async def test_exact_live_resolved_put_unresolved_read_miss(exact):
    # read-side hardening 핵심: live resolved put 을 routes 식 unresolved read 가
    # hit 하지 못한다(routes 는 tier 선택 전이라 slot/model 미상으로 조회).
    await exact.put("p", "live-A", llm_mode="live", **_SLOT_A)
    assert await exact.get("p", llm_mode="live") is None


@pytest.mark.asyncio
async def test_exact_old_prompt_only_row_miss_no_crash(exact, tmp_path):
    db_path = str(tmp_path / "exact.db")
    # 테이블 생성을 위해 init 트리거(get 이 _ensure_init 호출).
    assert await exact.get("init", llm_mode="mock") is None
    # 구 스킴(prompt 단독 해시) row 를 직접 삽입.
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO exact_cache "
            "(prompt_hash, prompt, response, created_at, hit_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (_hash("legacy only"), "legacy only", "OLD REPLY", now),
        )
        await conn.commit()
    # 네임스페이스 키와 불일치 → graceful miss, 크래시 없음.
    assert await exact.get("legacy only", llm_mode="mock") is None


@pytest.mark.asyncio
async def test_exact_live_unresolved_put_refused(exact):
    with pytest.raises(CachePolicyError):
        await exact.put("p", "r", llm_mode="live")  # slot/model unresolved


# ═══════════════════════════════════════════════════════════════════════════
# SemanticCache (ephemeral Chroma + 결정론적 fake EF — e5 미로드)
# ═══════════════════════════════════════════════════════════════════════════
chromadb = pytest.importorskip("chromadb")

from chromadb import Documents, EmbeddingFunction, Embeddings  # noqa: E402

from app.ingress.semantic_cache import SemanticCache  # noqa: E402


class _DeterministicEF(EmbeddingFunction[Documents]):
    """결정론적 경량 EF — 같은 텍스트 → 같은 벡터(같은 prompt 는 거리 0).

    네임스페이스 격리는 임베딩 품질과 무관하므로 e5 대신 이걸 쓴다.
    """

    def __init__(self) -> None:  # 미구현 시 chroma DeprecationWarning 회피.
        pass

    def __call__(self, input: Documents) -> Embeddings:
        out: Embeddings = []
        for text in input:
            h = sum((i + 1) * ord(c) for i, c in enumerate(str(text)))
            out.append([float((h % 9) + 1), float((h % 7) + 1), float((h % 5) + 1)])
        return out

    @staticmethod
    def name() -> str:
        return "deterministic_fake_ef"

    def get_config(self) -> dict:  # chroma 1.5.x EF 직렬화 계약(향후 필수).
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "_DeterministicEF":
        return _DeterministicEF()


@pytest.fixture(scope="module")
def chroma_client():
    return chromadb.EphemeralClient()


@pytest.fixture
def sem(chroma_client):
    # 테스트마다 빈 "semantic_cache" 컬렉션으로 초기화.
    try:
        chroma_client.delete_collection("semantic_cache")
    except Exception:  # pragma: no cover - 첫 테스트엔 컬렉션이 없음
        pass
    return SemanticCache(
        "unused", embedding_function=_DeterministicEF(), client=chroma_client,
    )


@pytest.mark.asyncio
async def test_semantic_mock_put_mock_get_hits(sem):
    await sem.put("hello", "mock-reply", llm_mode="mock")
    result = await sem.get("hello", llm_mode="mock")
    assert result is not None and result[0] == "mock-reply"


@pytest.mark.asyncio
async def test_semantic_live_slot_put_get_hits(sem):
    await sem.put("hello", "live-A-reply", llm_mode="live", **_SLOT_A)
    result = await sem.get("hello", llm_mode="live", **_SLOT_A)
    assert result is not None and result[0] == "live-A-reply"


@pytest.mark.asyncio
async def test_semantic_mock_live_coexist_no_overwrite(sem):
    # 같은 prompt 를 mock·live 로 저장해도 upsert id 가 달라 공존(no overwrite).
    await sem.put("same", "MOCK", llm_mode="mock")
    await sem.put("same", "LIVE_A", llm_mode="live", **_SLOT_A)
    assert sem.collection.count() == 2
    assert (await sem.get("same", llm_mode="mock"))[0] == "MOCK"
    assert (await sem.get("same", llm_mode="live", **_SLOT_A))[0] == "LIVE_A"


@pytest.mark.asyncio
async def test_semantic_live_slotA_slotB_coexist_no_overwrite(sem):
    await sem.put("same", "LIVE_A", llm_mode="live", **_SLOT_A)
    await sem.put("same", "LIVE_B", llm_mode="live", **_SLOT_B)
    assert sem.collection.count() == 2
    assert (await sem.get("same", llm_mode="live", **_SLOT_A))[0] == "LIVE_A"
    assert (await sem.get("same", llm_mode="live", **_SLOT_B))[0] == "LIVE_B"


@pytest.mark.asyncio
async def test_semantic_mock_live_cross_miss_both_directions(sem):
    await sem.put("hello", "MOCK", llm_mode="mock")
    assert await sem.get("hello", llm_mode="live", **_SLOT_A) is None
    await sem.put("world", "LIVE", llm_mode="live", **_SLOT_A)
    assert await sem.get("world", llm_mode="mock") is None


@pytest.mark.asyncio
async def test_semantic_live_slotA_slotC_cross_miss(sem):
    await sem.put("hello", "LIVE_A", llm_mode="live", **_SLOT_A)
    assert await sem.get("hello", llm_mode="live", **_SLOT_C) is None


@pytest.mark.asyncio
async def test_semantic_same_model_different_slot_cross_miss(sem):
    assert _SLOT_A["slot_fingerprint"] != _SLOT_B["slot_fingerprint"]
    assert _SLOT_A["model_id"] == _SLOT_B["model_id"]
    await sem.put("hello", "LIVE_A", llm_mode="live", **_SLOT_A)
    assert await sem.get("hello", llm_mode="live", **_SLOT_B) is None


@pytest.mark.asyncio
async def test_semantic_old_entry_miss_no_crash(sem):
    # 구 스킴: namespace 메타 없이 직접 upsert(프롬프트 단독 시절 모사).
    # collection.upsert 는 동기 chromadb API (SemanticCache 가 to_thread 로 감쌈).
    sem.collection.upsert(
        ids=["legacy-id"],
        documents=["legacy prompt"],
        metadatas=[{"response": "OLD"}],
    )
    assert await sem.get("legacy prompt", llm_mode="mock") is None


@pytest.mark.asyncio
async def test_semantic_retrieval_doc_without_response_miss_no_crash(sem):
    # retrieval corpus 식 문서(response 메타 없음, category 만) → where 에서 배제.
    sem.collection.upsert(
        ids=["ctx-1"],
        documents=["context chunk"],
        metadatas=[{"category": "coding", "source": "seed"}],
    )
    assert await sem.get("context chunk", llm_mode="mock") is None


@pytest.mark.asyncio
async def test_semantic_live_unresolved_put_refused(sem):
    with pytest.raises(CachePolicyError):
        await sem.put("p", "r", llm_mode="live")  # slot/model unresolved

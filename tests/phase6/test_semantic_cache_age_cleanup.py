"""B9 — SemanticCache.delete_older_than + created_at stamping (fake EF, ephemeral
ChromaDB; no e5). Proves age-based deletion, preservation of fresh / un-stamped
entries, batch bounding, and that the read path (get) is unaffected.
"""
from __future__ import annotations

import time

import pytest

chromadb = pytest.importorskip("chromadb")

from chromadb import Documents, EmbeddingFunction, Embeddings  # noqa: E402

from app.ingress.cache_key import semantic_id, semantic_metadata  # noqa: E402
from app.ingress.semantic_cache import SemanticCache  # noqa: E402


class _DeterministicEF(EmbeddingFunction[Documents]):
    def __init__(self) -> None:
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

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "_DeterministicEF":
        return _DeterministicEF()


@pytest.fixture
def sem():
    client = chromadb.EphemeralClient()
    try:
        client.delete_collection("semantic_cache")
    except Exception:  # pragma: no cover
        pass
    return SemanticCache("unused", embedding_function=_DeterministicEF(), client=client)


@pytest.mark.asyncio
async def test_put_stamps_created_at(sem):
    await sem.put("hello", "world")
    got = sem.collection.get(where={"created_at": {"$gt": 0.0}})
    assert got["ids"], "entry should carry a numeric created_at"


@pytest.mark.asyncio
async def test_old_entries_deleted(sem):
    await sem.put("p1", "r1")
    await sem.put("p2", "r2")
    # now far in the future → both entries are 'older than 1s'.
    deleted = await sem.delete_older_than(now=time.time() + 1e6, max_age_s=1.0, batch_limit=100)
    assert deleted == 2
    assert await sem.get("p1") is None
    assert await sem.get("p2") is None


@pytest.mark.asyncio
async def test_fresh_entries_preserved(sem):
    await sem.put("keep", "r")
    # huge max_age_s → cutoff far in the past → nothing is old enough.
    deleted = await sem.delete_older_than(now=time.time(), max_age_s=1e9, batch_limit=100)
    assert deleted == 0
    assert await sem.get("keep") is not None


@pytest.mark.asyncio
async def test_entry_without_created_at_is_preserved(sem):
    # an entry predating B9 (no created_at) must never match the $lt filter.
    sem.collection.upsert(
        ids=["legacy"],
        documents=["legacy-doc"],
        metadatas=[{"cache_schema": "x", "cache_kind": "answer_cache"}],
    )
    deleted = await sem.delete_older_than(now=time.time() + 1e6, max_age_s=1.0, batch_limit=100)
    assert deleted == 0
    assert sem.collection.get(ids=["legacy"])["ids"] == ["legacy"]


@pytest.mark.asyncio
async def test_batch_limit_bounds_deletion(sem):
    for i in range(5):
        await sem.put(f"p{i}", f"r{i}")
    deleted = await sem.delete_older_than(now=time.time() + 1e6, max_age_s=1.0, batch_limit=2)
    assert deleted == 2  # only batch_limit removed this cycle
    remaining = sem.collection.get()["ids"]
    assert len(remaining) == 3


@pytest.mark.asyncio
async def test_read_path_unaffected_by_created_at(sem):
    # created_at is additive metadata; get() still hits on similarity.
    await sem.put("question", "answer")
    hit = await sem.get("question", threshold=0.0)
    assert hit is not None
    assert hit[0] == "answer"

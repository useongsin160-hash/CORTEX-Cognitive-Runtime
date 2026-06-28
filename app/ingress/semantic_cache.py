"""Tier-2 Semantic Cache (ChromaDB, sub-50ms)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import chromadb

from app.core.embedder import get_embedding_function
from app.ingress.cache_key import (
    resolve_write_namespace,
    semantic_id,
    semantic_metadata,
    semantic_where,
)

_COLLECTION_NAME = "semantic_cache"


class SemanticCache:
    """Cosine-similarity cache backed by a persistent Chroma collection."""

    def __init__(
        self,
        chroma_path: str,
        *,
        embedding_function: Any | None = None,
        client: Any | None = None,
    ) -> None:
        # client/embedding_function 주입은 테스트용 — 결정론적 fake EF + ephemeral
        # client 로 e5 가중치 반복 로드(Windows 네이티브 크래시 위험) 없이
        # 네임스페이스 격리를 검증한다. production 은 둘 다 None →
        # PersistentClient(chroma_path) + multilingual-e5-base(불변).
        if client is None:
            Path(chroma_path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=chroma_path)
        self._client = client
        # hnsw:space=cosine pins distance ∈ [0, 2] so similarity = 1 - dist
        # is monotonic and comparable to the 0.90 threshold contract.
        # embedding_function pins the multilingual-e5-base model so
        # SemanticCache and CentroidStore share the same vector space.
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=embedding_function or get_embedding_function(),
        )

    @property
    def collection(self):
        """Phase 4 STEP 3.3b — ChromaDBSearcher가 접근할 수 있도록 노출.

        SemanticCache는 여전히 단일 소유자다 (lifespan에서 생성·warmup).
        외부는 읽기 전용으로 collection.query 등을 호출할 수 있다.
        """
        return self._collection

    def close(self) -> None:
        """Chroma client 핸들을 결정론적으로 해제한다 (멱등).

        성격: **크래시 수정이 아니라 리소스 누수 위생 + DI 시드**다. 프로덕션
        lifespan(프로세스당 1회) 종료와 테스트 teardown이 **공유하는 실 메서드**이며,
        app/ 에 환경 감지 분기를 넣지 않기 위한 단일 수명주기 훅이다. 호출 뒤
        get/put 은 더 이상 사용하지 않는다(수명주기 종료).
        """
        client = getattr(self, "_client", None)
        if client is None:
            return
        self._collection = None
        self._client = None
        # 프로세스 전역 system cache 를 비워 native 핸들 GC 를 결정론화한다.
        # 버전별 부재/실패는 무시(위생 목적이라 best-effort).
        try:
            from chromadb.api.client import SharedSystemClient
            SharedSystemClient.clear_system_cache()
        except Exception:
            pass

    async def get(
        self,
        prompt: str,
        threshold: float = 0.90,
        *,
        llm_mode: str = "mock",
        slot_fingerprint: str | None = None,
        model_id: str | None = None,
    ) -> tuple[str, float] | None:
        # where 필터가 네임스페이스(cache_schema/kind/llm_mode/slot_fp/model_id)를
        # 강제한다 → 다른 mode/slot 엔트리와 retrieval corpus·구 엔트리(해당 필드
        # 부재)는 검색 단계에서 제외되어 graceful miss 가 된다. read 는 정책 강제
        # 없이 그대로 조회한다(live unresolved 조회는 허용·항상 miss).
        where = semantic_where(
            llm_mode=llm_mode,
            slot_fingerprint=slot_fingerprint,
            model_id=model_id,
        )
        result: dict[str, Any] = await asyncio.to_thread(
            self._collection.query,
            query_texts=[prompt],
            n_results=1,
            where=where,
        )
        ids = result.get("ids") or [[]]
        if not ids[0]:
            return None
        distance = result["distances"][0][0]
        similarity = 1.0 - float(distance)
        if similarity < threshold:
            return None
        metadatas = result.get("metadatas") or [[None]]
        meta = metadatas[0][0] or {}
        response = meta.get("response")
        # 방어 2중화: where 로 걸렀어도 response 메타가 없으면 예외가 아니라 miss.
        if not isinstance(response, str):
            return None
        return response, similarity

    async def put(
        self,
        prompt: str,
        response: str,
        *,
        llm_mode: str = "mock",
        slot_fingerprint: str | None = None,
        model_id: str | None = None,
    ) -> None:
        # write 는 네임스페이스를 확정·정책 강제한다(live unresolved write 거부).
        mode, fp, mid = resolve_write_namespace(
            llm_mode=llm_mode,
            slot_fingerprint=slot_fingerprint,
            model_id=model_id,
        )
        # upsert id 가 네임스페이스를 포함 → 같은 prompt 라도 mock/live/slotA/slotB
        # 엔트리가 서로 덮어쓰지 않고 공존한다.
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[semantic_id(prompt, llm_mode=mode, slot_fingerprint=fp, model_id=mid)],
            documents=[prompt],
            metadatas=[
                semantic_metadata(
                    llm_mode=mode,
                    slot_fingerprint=fp,
                    model_id=mid,
                    response=response,
                    created_at=time.time(),
                )
            ],
        )

    async def delete_older_than(
        self, now: float, max_age_s: float, batch_limit: int
    ) -> int:
        """B9 (GlymphaticCleaner) — delete cache entries older than the cutoff.

        Age comes from the numeric ``created_at`` metadata stamped by put(); the
        ``$lt`` filter is a numeric comparison, so entries that predate that field
        (no ``created_at``) never match and are preserved (graceful). ``limit``
        bounds the fetch and ``batch_limit`` bounds the delete, so a single cycle
        can remove at most ``batch_limit`` entries. Read paths (get/query/where)
        are untouched — this method only ever deletes.
        """
        collection = self._collection
        if collection is None:  # closed (lifespan teardown); nothing to clean.
            return 0
        cutoff = now - max_age_s
        found: dict[str, Any] = await asyncio.to_thread(
            collection.get,
            where={"created_at": {"$lt": cutoff}},
            limit=batch_limit,
        )
        ids = found.get("ids") or []
        if not ids:
            return 0
        await asyncio.to_thread(collection.delete, ids=ids)
        return len(ids)

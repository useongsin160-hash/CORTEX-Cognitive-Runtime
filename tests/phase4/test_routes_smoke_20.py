"""Phase 4 STEP 3.3c — 20쿼리 smoke test.

routes.py 통합 후 첫 통합 회귀. path 분포와 swarm_trace 구조를 검증한다.
100쿼리 full regression은 STEP 5에서.

ChromaDB 시드 정책:
  - ExactCache: tmp 파일 + put()으로 사전 적재
  - SemanticCache: tmp chroma 디렉토리 + put()으로 사전 적재
  - Swarm의 chromadb collection은 lifespan 기본값 그대로 (별도 시드 X)
    → routed 경로의 context_status는 대부분 "empty" 예상 (결정 2: 정상)
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ingress.exact_cache import ExactCache
from app.ingress.semantic_cache import SemanticCache
from app.main import app

_VALID_SOURCES = {"thalamus", "exact_cache", "semantic_cache",
                  "tier_1_5", "swarm", "fallback"}
_EARLY_EXIT = {"thalamus", "exact_cache", "semantic_cache", "tier_1_5"}

SMOKE_QUERIES: list[dict] = [
    # ── thalamus 4건 (≤20자, greetings/short prompts) ───────────────────
    {"text": "안녕", "expected_source": "thalamus"},
    {"text": "ㅎㅇ", "expected_source": "thalamus"},
    {"text": "ping", "expected_source": "thalamus"},
    {"text": "thanks", "expected_source": "thalamus"},

    # ── exact_cache 4건 (사전 시드 + 동일 쿼리 호출) ─────────────────────
    {"text": "Hello world today friend", "expected_source": "exact_cache",
     "seed_exact": True},
    {"text": "How are you doing today", "expected_source": "exact_cache",
     "seed_exact": True},
    {"text": "What is the weather like today", "expected_source": "exact_cache",
     "seed_exact": True},
    {"text": "Tell me about yourself today", "expected_source": "exact_cache",
     "seed_exact": True},

    # ── semantic_cache 4건 (시드 = 쿼리 동일 → sim≈1.0 → ≥0.90 cache hit) ─
    {"text": "Capital city of France is Paris", "expected_source": "semantic_cache",
     "seed_semantic": True},
    {"text": "Python is a programming language", "expected_source": "semantic_cache",
     "seed_semantic": True},
    {"text": "Earth orbits around the Sun", "expected_source": "semantic_cache",
     "seed_semantic": True},
    {"text": "Water freezes at zero Celsius", "expected_source": "semantic_cache",
     "seed_semantic": True},

    # ── tier_1_5 4건 (difficulty 1 + sub-0.90 semantic) ─────────────────
    # 실제 sim 측정 결과에 따라 routed_lightweight로 흘러갈 수 있음 — 보고에 명시.
    {"text": "What time is dinner today", "expected_source": "tier_1_5"},
    {"text": "Where is the cafeteria today", "expected_source": "tier_1_5"},
    {"text": "When does the meeting start", "expected_source": "tier_1_5"},
    {"text": "Who is the manager today", "expected_source": "tier_1_5"},

    # ── routed (swarm) 4건 (HARD keyword + 복잡) ───────────────────────
    {"text": "Implement async retry logic with exponential backoff in Python",
     "expected_source": "swarm"},
    {"text": "Analyze the time complexity of merge sort vs quicksort",
     "expected_source": "swarm"},
    {"text": "Design a distributed cache system with consistency guarantees",
     "expected_source": "swarm"},
    {"text": "Compare REST and GraphQL for microservice architecture",
     "expected_source": "swarm"},
]


@pytest.fixture
def client(app_client, make_ephemeral_cache) -> Iterator[TestClient]:
    """app_client (per-test tmp ExactCache + app.state 원복) + 실 e5 ephemeral
    semantic store(독립 in-memory, PersistentClient 누수/락 없음), pre-seeded for smoke."""
    c = app_client
    c.app.state.semantic_cache = make_ephemeral_cache(real=True)

    async def _seed() -> None:
        for q in SMOKE_QUERIES:
            if q.get("seed_exact"):
                await c.app.state.exact_cache.put(
                    q["text"], f"[seeded exact] {q['text']}",
                )
            if q.get("seed_semantic"):
                await c.app.state.semantic_cache.put(
                    q["text"], f"[seeded semantic] {q['text']}",
                )

    asyncio.run(_seed())
    yield c


def test_smoke_20_queries(client):
    """20쿼리 smoke — HTTP/source/swarm_trace 구조 검증."""
    results: list[dict] = []
    for q in SMOKE_QUERIES:
        resp = client.post("/query", json={"prompt": q["text"]})
        assert resp.status_code == 200, f"non-200 for: {q['text']}"
        body = resp.json()
        results.append({
            "query": q["text"],
            "expected": q["expected_source"],
            "actual": body["response_source"],
            "path_taken": body["path_taken"],
            "swarm_trace": body["swarm_trace"],
            "selected_tier": body.get("selected_tier"),
        })

    # 1. response_source 값이 모두 enum 6값 중 하나.
    for r in results:
        assert r["actual"] in _VALID_SOURCES, (
            f"invalid response_source={r['actual']} for '{r['query']}'"
        )

    # 2. early-exit 4경로 모두 swarm_trace=None.
    early_exit = [r for r in results if r["actual"] in _EARLY_EXIT]
    for r in early_exit:
        assert r["swarm_trace"] is None, (
            f"early-exit '{r['actual']}' carries swarm_trace: {r['query']}"
        )

    # 3. routed/swarm 경로는 swarm_trace 채워짐.
    routed = [r for r in results if r["actual"] == "swarm"]
    for r in routed:
        st = r["swarm_trace"]
        assert st is not None, f"routed missing swarm_trace: {r['query']}"
        assert st["executed"] is True
        assert st["status"] in {"ok", "degraded", "error", "timeout"}
        assert st["elapsed_ms"] is not None and st["elapsed_ms"] > 0
        assert st["plan_intent"] is not None

    # 4. routed 경로 최소 1건 (예상 4건이지만 LC 분류에 따라 변동 가능).
    assert len(routed) >= 1, "no routed queries observed in smoke"

    # 5. 분포 보고 (print) — 사후 분석용.
    dist = Counter(r["actual"] for r in results)
    print("\n=== STEP 3.3c smoke — actual path distribution ===")
    for source in ("thalamus", "exact_cache", "semantic_cache",
                   "tier_1_5", "swarm", "fallback"):
        print(f"  {source:16s}: {dist.get(source, 0)}")

    mismatches = [r for r in results if r["expected"] != r["actual"]]
    print(f"\nexpected vs actual mismatches: {len(mismatches)} / {len(results)}")
    for r in mismatches:
        print(f"  expected={r['expected']:16s} actual={r['actual']:16s} "
              f"text={r['query'][:60]}")

    print("\nrouted swarm_trace samples:")
    for r in routed[:2]:
        st = r["swarm_trace"]
        print(f"  status={st['status']}  ctx={st['context_status']}  "
              f"plan={st['plan_intent']}  elapsed={st['elapsed_ms']:.1f}ms")


def test_smoke_context_status_distribution(client):
    """routed 경로의 context_status 분포 — error/timeout이 절반 미만이어야 한다."""
    routed_queries = [q for q in SMOKE_QUERIES if q["expected_source"] == "swarm"]

    statuses: list[str] = []
    for q in routed_queries:
        resp = client.post("/query", json={"prompt": q["text"]})
        body = resp.json()
        if body.get("swarm_trace"):
            statuses.append(body["swarm_trace"].get("context_status"))

    dist = Counter(statuses)
    print(f"\ncontext_status distribution (routed only): {dict(dist)}")

    if statuses:
        bad = dist.get("error", 0) + dist.get("timeout", 0)
        assert bad <= len(statuses) // 2, (
            f"too many error/timeout in context_status: {dist}"
        )

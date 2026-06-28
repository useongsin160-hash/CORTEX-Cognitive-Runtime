"""Phase 3 STEP 2 — SemanticEvaluator under centroid lookup.

Exercises the real CentroidStore (multilingual-e5-base + mean-centered
bilingual centroids) injected into the evaluator.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.routing.centroid_store import CentroidStore
from app.routing.semantic_evaluator import SemanticEvaluator

SEED_PATH = Path(__file__).parent / "seed_queries.json"


@pytest.fixture(scope="module")
async def evaluator(tmp_path_factory) -> SemanticEvaluator:
    cache = tmp_path_factory.mktemp("eval_v2_centroids") / "centroids.npz"
    store = CentroidStore(cache_path=cache)
    await store.build_from_seeds(SEED_PATH)
    return SemanticEvaluator(centroid_store=store)


# 14 cross-lingual probes — 2 per category, one English / one Korean.
# Both sides must classify to the documented category.
_CROSS_LINGUAL = [
    ("debug this null pointer in the service layer", "coding"),
    ("이 함수에서 NullPointerException 디버깅", "coding"),
    ("design a boss phase transition mechanic", "game_design"),
    ("보스 페이즈 전환 메커니즘 설계", "game_design"),
    ("prove this theorem by induction", "math_logic"),
    ("이 정리를 귀납법으로 증명", "math_logic"),
    ("draft a corporate blog post for a launch", "writing"),
    ("신제품 출시용 기업 블로그 글 초안", "writing"),
    ("build a logistic regression for churn prediction", "data_analysis"),
    ("이탈 예측용 로지스틱 회귀 모델 만들기", "data_analysis"),
    ("design a payments architecture across regions", "system_design"),
    ("멀티 리전 결제 시스템 아키텍처 설계", "system_design"),
    ("recommend a movie to watch tonight", "general"),
    ("오늘 저녁 볼만한 영화 추천", "general"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,expected",
    _CROSS_LINGUAL,
    ids=[f"{cat}-{i}" for i, (_, cat) in enumerate(_CROSS_LINGUAL)],
)
async def test_cross_lingual_classification(evaluator, prompt, expected):
    result = await evaluator.evaluate(prompt)
    assert result.category == expected, (
        f"prompt '{prompt}' → got {result.category}, expected {expected}"
    )
    assert result.classification_method == "centroid"


@pytest.mark.asyncio
async def test_similarity_range(evaluator):
    """The similarity in EvaluationResult lives in the mean-centered
    frame, theoretical range [-1, 1]. Pydantic enforces the absolute
    bound; this test additionally probes the production path."""
    extremes: list[float] = []
    for prompt, _ in _CROSS_LINGUAL:
        r = await evaluator.evaluate(prompt)
        extremes.append(r.similarity)
        assert -1.0 <= r.similarity <= 1.0
    # Healthy seed matches almost always land on a positive cosine.
    positive_count = sum(1 for s in extremes if s > 0)
    assert positive_count >= len(extremes) // 2, (
        f"too many non-positive similarities: {extremes}"
    )


@pytest.mark.asyncio
async def test_embedding_is_populated(evaluator):
    """STEP 2 contract: EvaluationResult.embedding must carry the raw
    e5 vector (768-dim) so downstream layers can reuse it without
    re-embedding."""
    r = await evaluator.evaluate("how do I write a python decorator?")
    assert isinstance(r.embedding, list)
    assert len(r.embedding) == 768
    assert all(isinstance(x, float) for x in r.embedding[:5])


@pytest.mark.asyncio
async def test_difficulty_independent_of_category(evaluator):
    """Difficulty heuristic is independent of the category lookup:
    a HARD-keyword prompt and a plain one in the same category should
    yield different difficulties but the same category."""
    hard = await evaluator.evaluate(
        "design a payments architecture across regions with tradeoffs"
    )
    easy = await evaluator.evaluate("결제 시스템 알려줘")
    assert hard.category == easy.category == "system_design"
    assert hard.difficulty == 4  # B12: HARD keyword → VERY_HARD(4)
    assert easy.difficulty == 1


@pytest.mark.asyncio
async def test_classification_method_is_centroid(evaluator):
    r = await evaluator.evaluate("debug this typescript hook")
    assert r.classification_method == "centroid"


@pytest.mark.asyncio
async def test_confidence_in_unit_interval(evaluator):
    for prompt, _ in _CROSS_LINGUAL:
        r = await evaluator.evaluate(prompt)
        assert 0.0 <= r.confidence <= 1.0


@pytest.mark.asyncio
async def test_evaluate_latency_budget(evaluator):
    """Response-time gate after the STEP 1.5 embedder swap.

    The design's original < 20 ms target presumed the lightweight
    all-MiniLM-L6-v2 embedder. Phase 3 STEP 1.5 swapped that out for
    intfloat/multilingual-e5-base (278 M params) to clear the
    cross-lingual 7/7 acceptance gate; the embedder inference itself
    now consumes 35-80 ms on CPU and is the floor for any single
    evaluate() call.

    STEP 1.5 multilingual-e5-base 도입에 따른 재산정.
    설계 결정 기록: docs/adr/ADR-001-evaluator-latency.md 참조.
    Phase 4 최적화 대상: docs/adr/ADR-002-phase4-shared-embedding-pipeline.md
    """
    import time

    # Warmup so we measure steady-state, not the first-call model load.
    await evaluator.evaluate("warmup query about anything")

    prompts = [p for p, _ in _CROSS_LINGUAL]
    timings: list[float] = []
    for _ in range(3):  # 3 rounds × 14 prompts = 42 samples
        for prompt in prompts:
            t0 = time.perf_counter()
            await evaluator.evaluate(prompt)
            timings.append((time.perf_counter() - t0) * 1000.0)

    timings.sort()
    n = len(timings)
    avg = sum(timings) / n
    p99 = timings[min(int(n * 0.99), n - 1)]

    AVG_BUDGET_MS = 80.0   # ADR-001 re-baselined target
    P99_BUDGET_MS = 150.0  # ADR-001 re-baselined target
    assert avg < AVG_BUDGET_MS, f"avg {avg:.1f} ms >= {AVG_BUDGET_MS} ms budget"
    assert p99 < P99_BUDGET_MS, f"p99 {p99:.1f} ms >= {P99_BUDGET_MS} ms budget"

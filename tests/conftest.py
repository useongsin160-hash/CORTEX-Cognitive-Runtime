"""Root test fixtures — single-process isolation (OVERTURE A5).

목표: `pytest tests/` 를 **한 프로세스**로 돌려도 green. 세 가지 격리를 보장한다.

1. **공유 ./data/ 실 store 미접촉**: settings 의 database_url/chroma_path 를 세션 tmp 로
   리다이렉트한다. 이 작업은 반드시 **앱 import 前 · get_settings() lru_cache prime 前**
   이어야 한다 — pytest 는 이 root conftest 를 test 모듈보다 먼저 로드하므로, 모듈
   최상단(import-time)에서 env 를 덮고 캐시를 prime + assert 로 타이밍을 검증한다.
2. **PersistentClient/torch 과다 생성 차단**: 세션 1회만 lifespan 을 진입(warmup·centroid·
   e5 1회)시키고, 진입 前 semantic_cache 를 **EphemeralClient + 공유 임베더**로 교체한다.
   PersistentClient 는 어떤 테스트도 실제로 쓰지 않는다(teardown 크래시 차단).
3. **테스트 DI 한정**: EphemeralClient·공유 임베더·fake EF 는 여기(conftest)에만 있다.
   app/ 에는 환경 감지 분기가 없다(프로덕션과 테스트가 같은 코드를 돈다).

네이티브 로드 순서 주의: torch(sentence-transformers)/chromadb 는 collection 시점에
import 하지 않는다(Windows 네이티브 DLL 로드 순서 교란 → access violation). 무거운
import 는 fixture 실행 시점으로 지연해 기존 테스트와 동일한 순서를 유지한다. 모듈
top-level 은 가벼운 app.core.config 만 건드린다.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# ── 1) 앱 import 前 env 리다이렉트 (import-time, 최우선) ─────────────────────
_SESSION_TMP = tempfile.mkdtemp(prefix="cortex_aev_tests_").replace("\\", "/")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _SESSION_TMP + "/cortex_memory.db"
os.environ["CHROMA_PATH"] = _SESSION_TMP + "/cortex_chroma_db"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
# transformers 5.x 는 모델 텐서를 ThreadPoolExecutor 로 비동기 materialize 한다. 임베더
# 첫 로드가 semantic_cache.get → asyncio.to_thread(워커 스레드) 안에서 일어나면, 그 워커가
# 다시 로딩 스레드를 spawn 하는 중첩 스레딩이 Windows+torch(CPU) 에서 네이티브 access
# violation(0xC0000005)을 낸다. async load 를 끄면 어느 스레드든 순차 materialize 라
# 크래시가 사라진다(core_model_loading.py 가 공식 지원하는 토글). 성격: 테스트 하버스
# 안정화 env(앱 코드 무변경·회피 아닌 메커니즘 제거). subprocess 도 env 상속으로 함께 안정화.
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

# get_settings() lru_cache 를 tmp 로 prime + 타이밍 검증. app.core.config 는 torch/chromadb
# 를 끌어오지 않는 경량 모듈이라 collection 시점 import 가 안전하다. 캐시를 tmp 로 굳히면
# 이후 어떤 create_app() 도 ./data/ 를 만들지 않는다.
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()
_primed = get_settings()
assert _SESSION_TMP in _primed.chroma_path, (
    f"A5 conftest: CHROMA_PATH redirect failed before app import (got {_primed.chroma_path!r})"
)
assert _SESSION_TMP in _primed.database_url, (
    f"A5 conftest: DATABASE_URL redirect failed before app import (got {_primed.database_url!r})"
)


# ── 2) 공유 임베더 (프로세스 1회 로드) ──────────────────────────────────────
@pytest.fixture(scope="session")
def shared_embedder():
    """실 multilingual-e5-base 임베더(앱 싱글톤). 프로세스 내 1회만 로드된다.

    여기서 **메인 스레드**로 모델 가중치를 미리 materialize 한다. 이후 SemanticCache 의
    asyncio.to_thread(워커 스레드) 경로는 encode 만(싱글톤 재사용) 하므로, Windows+torch
    에서 워커 스레드 텐서 materialize 가 내는 네이티브 access violation(0xC0000005)을
    피한다. (materialize 는 메인 스레드에서 안정적, encode 는 워커에서 안전.)
    """
    from app.core.embedder import get_embedding_function

    ef = get_embedding_function()
    ef(["warmup"])  # 메인 스레드 materialize (_get_model())
    return ef


# ── 3) 결정론 fake EF (e5/torch 미로드) ─────────────────────────────────────
class DeterministicEF:
    """입력 문자열 → 고정 16차원 결정론 벡터. torch/e5 없이 chromadb 동작 검증용."""

    def __call__(self, input):  # noqa: A002 (chromadb EF 시그니처)
        import hashlib

        vectors = []
        for text in input:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            vectors.append([b / 255.0 for b in digest[:16]])
        return vectors

    @staticmethod
    def name() -> str:
        return "deterministic-test-ef"


@pytest.fixture(scope="session")
def fake_ef() -> "DeterministicEF":
    return DeterministicEF()


# ── 4) Ephemeral SemanticCache 팩토리 (PersistentClient 누수 0) ─────────────
def _make_ephemeral_cache(*, embedding_function):
    """EphemeralClient(in-memory) + 주입 EF 로 SemanticCache 를 만든다.
    PersistentClient 를 만들지 않아 teardown 크래시·./data 오염이 없다."""
    import chromadb
    from chromadb.api.client import SharedSystemClient

    from app.ingress.semantic_cache import SemanticCache

    # chromadb EphemeralClient 는 기본 Settings 가 같으면 프로세스 전역 in-memory system 을
    # 공유한다 → 서로 다른 EF(실 e5 vs 결정론 fake)로 같은 "semantic_cache" 컬렉션을 열면
    # EF 충돌(ValueError). 생성 직전 system cache 를 비워 각 ephemeral 캐시에 독립 system 을
    # 부여한다. 기존 client 핸들은 자기 System 객체 참조를 유지하므로 무효화되지 않는다.
    SharedSystemClient.clear_system_cache()
    return SemanticCache(
        chroma_path="<ephemeral-unused>",
        embedding_function=embedding_function,
        client=chromadb.EphemeralClient(),
    )


@pytest.fixture
def make_ephemeral_cache(shared_embedder, fake_ef):
    """테스트가 ephemeral SemanticCache 를 만들고, teardown 에서 close() 한다.
    `real=True` → 공유 실 임베더, `real=False` → 결정론 fake EF."""
    created = []

    def _factory(*, real: bool = True):
        cache = _make_ephemeral_cache(
            embedding_function=shared_embedder if real else fake_ef
        )
        created.append(cache)
        return cache

    yield _factory
    for cache in created:
        cache.close()


# ── 5) 세션 app — lifespan 1회 진입(warmup·centroid·e5 1회) ─────────────────
@pytest.fixture(scope="session")
def _session_app(shared_embedder):
    """모듈 전역 app 을 세션당 1회만 lifespan 진입시킨다.

    진입 前 semantic_cache 를 ephemeral(실 임베더)로 교체 → warmup·centroid 가
    PersistentClient 없이 동작한다(./data 미접촉, 실 client churn 0). 세션 종료 시
    lifespan shutdown 이 close() 를 1회 호출(프로덕션 단일 수명주기와 동일).
    """
    from fastapi.testclient import TestClient

    from app.main import app

    app.state.semantic_cache = _make_ephemeral_cache(embedding_function=shared_embedder)
    with TestClient(app) as client:
        yield client


# ── 6) 함수 스코프 client — 세션 app 대여 + app.state 오버라이드 save/restore ─
@pytest.fixture
def app_client(_session_app, tmp_path):
    """세션 entered app 을 빌려준다. 각 테스트는 app.state.semantic_cache /
    exact_cache 를 자유로이 교체하고, 끝나면 원복된다(테스트 간 누수 차단).
    기본으로 per-test tmp ExactCache 를 깐다(공유 db 락 방지)."""
    from app.ingress.exact_cache import ExactCache

    app = _session_app.app
    saved_semantic = app.state.semantic_cache
    saved_exact = app.state.exact_cache
    app.state.exact_cache = ExactCache(str(tmp_path / "exact.db"))
    try:
        yield _session_app
    finally:
        app.state.semantic_cache = saved_semantic
        app.state.exact_cache = saved_exact

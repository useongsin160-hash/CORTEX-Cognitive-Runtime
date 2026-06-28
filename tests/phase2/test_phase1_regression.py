"""Guarantees that adding Phase 2 modules did not regress Phase 1."""
import gc
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_phase1_suite_still_passes():
    # 단일 프로세스 전체 실행에서는 부모가 이미 multilingual-e5-base(~1GB)를 상주시킨
    # 상태일 수 있다(앞선 semantic 테스트가 로드). 자식 subprocess 가 e5 를 또 로드하면
    # 부모+자식 동시 상주가 Windows commit 한계(페이징 파일)를 넘겨 OSError 1455 로
    # 죽는다. subprocess 직전 부모의 e5 모델 싱글톤을 해제해 동시 상주를 피한다 — 이후
    # 부모 테스트가 필요 시 lazy 재로드한다(같은 가중치·같은 벡터, 동작 불변). 임베더
    # 래퍼(get_embedding_function)는 그대로 유효하고 모델 가중치만 비운다.
    from app.core import embedder as _emb

    _emb._model_singleton = None
    gc.collect()

    # async tensor load 비활성(중첩 스레드 네이티브 크래시 회피)을 자식에 명시 전달.
    # (근본 안정화는 app/core/embedder 의 disable_mmap=True — mmap 페이지 폴트 제거.
    # 이 free + 자식 env 는 메모리 동시 상주 방지용 belt-and-suspenders 로 유지.)
    child_env = {**os.environ, "HF_DEACTIVATE_ASYNC_LOAD": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/phase1/", "-v"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=240,
            env=child_env,
        )
    finally:
        # 자식 종료 후 부모의 e5 를 **메인 스레드에서** 미리 재materialize 한다. 해제만
        # 하고 두면 다음 부모 사용처(세션 app warmup)가 asyncio.to_thread 워커 스레드
        # 안에서 재materialize 하게 되는데, Windows+torch 에서 워커 스레드 텐서
        # materialize 는 네이티브 access violation(0xC0000005)을 낸다. 메인 스레드에서
        # 미리 로드해 두면 이후 to_thread 는 encode 만(싱글톤 재사용) 하므로 안전하다.
        # (가중치 파일은 OS 캐시에 남아 재로드는 수 초.)
        _emb._get_model()
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"phase1 regression failed:\n{output}"
    assert "passed" in proc.stdout

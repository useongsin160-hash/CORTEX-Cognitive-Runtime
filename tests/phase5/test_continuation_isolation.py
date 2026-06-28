"""Phase 5 STEP 5 — Continuation 통합 모듈 isolation 검증.

cue_classifier.py / continuation_detector.py가 금지된 모듈을 import하지 않는지
정적으로 확인한다.
"""
from __future__ import annotations

import ast
from pathlib import Path


_CUE_CLASSIFIER_PY = Path("app/routing/cue_classifier.py")
_DETECTOR_PY = Path("app/routing/continuation_detector.py")
_PFC_PY = Path("app/routing/pfc.py")


def _get_imports(path: Path) -> set[str]:
    """파일에서 모든 import 모듈명 수집."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _source_contains(path: Path, text: str) -> bool:
    return text in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# cue_classifier.py — LLM / embedder import 0건
# ---------------------------------------------------------------------------


def test_cue_classifier_no_llm_import():
    imports = _get_imports(_CUE_CLASSIFIER_PY)
    assert not any(
        "llm_client" in i or "live_llm" in i or "anthropic" in i.lower()
        for i in imports
    )


def test_cue_classifier_no_embedder_import():
    imports = _get_imports(_CUE_CLASSIFIER_PY)
    assert not any("embedder" in i or "embedding" in i.lower() for i in imports)


def test_cue_classifier_no_chromadb():
    imports = _get_imports(_CUE_CLASSIFIER_PY)
    assert not any("chromadb" in i or "chroma" in i.lower() for i in imports)


def test_cue_classifier_no_sentence_transformers():
    imports = _get_imports(_CUE_CLASSIFIER_PY)
    assert not any("sentence_transformer" in i for i in imports)


# ---------------------------------------------------------------------------
# continuation_detector.py — PFC.infer_hint() 호출 0건
# ---------------------------------------------------------------------------


def test_detector_does_not_call_pfc_infer_hint():
    """ContinuationDetector는 PFC.infer_hint를 호출하지 않는다."""
    source = _DETECTOR_PY.read_text(encoding="utf-8")
    assert "infer_hint" not in source


def test_detector_does_not_import_pfc():
    """ContinuationDetector는 PFC 본체를 import 하지 않는다."""
    imports = _get_imports(_DETECTOR_PY)
    assert "app.routing.pfc" not in imports
    assert not any(i.endswith(".pfc") and "routing" in i for i in imports)


# ---------------------------------------------------------------------------
# continuation_detector.py — store mutation 메서드 호출 0건
# ---------------------------------------------------------------------------


def test_detector_does_not_call_store_mutation():
    """Detector source에 mutation method 호출이 없어야 한다."""
    source = _DETECTOR_PY.read_text(encoding="utf-8")
    forbidden = [
        ".touch(",
        ".set_active(",
        ".add_goal(",
        ".remove(",
        ".update(",
        ".delete_session(",
        ".delete_trace(",
    ]
    for token in forbidden:
        assert token not in source, f"detector contains mutation call: {token}"


def test_detector_no_llm_import():
    imports = _get_imports(_DETECTOR_PY)
    assert not any("llm_client" in i or "live_llm" in i for i in imports)


def test_detector_no_embedder_import():
    imports = _get_imports(_DETECTOR_PY)
    assert not any("embedder" in i for i in imports)


# ---------------------------------------------------------------------------
# Phase 6 모듈 import 0건
# ---------------------------------------------------------------------------


_PHASE6 = ["dopamine", "basal_ganglia", "rpe", "cr"]


def test_cue_classifier_no_phase6_imports():
    imports = _get_imports(_CUE_CLASSIFIER_PY)
    for mod in _PHASE6:
        assert not any(mod in i for i in imports)


def test_detector_no_phase6_imports():
    imports = _get_imports(_DETECTOR_PY)
    for mod in _PHASE6:
        assert not any(mod in i for i in imports)


def test_pfc_no_phase6_imports():
    imports = _get_imports(_PFC_PY)
    for mod in _PHASE6:
        assert not any(mod in i for i in imports)


# ---------------------------------------------------------------------------
# legacy/ import 0건
# ---------------------------------------------------------------------------


def test_cue_classifier_no_legacy_import():
    assert not _source_contains(_CUE_CLASSIFIER_PY, "legacy")


def test_detector_no_legacy_import():
    assert not _source_contains(_DETECTOR_PY, "legacy")


# ---------------------------------------------------------------------------
# response_source 신규 값 0건 (routes.py에 'continuation_bypass' 같은 신규 값 없음)
# ---------------------------------------------------------------------------


def test_routes_no_new_response_source():
    routes_py = Path("app/api/routes.py")
    source = routes_py.read_text(encoding="utf-8")
    # response_source는 기존 6종만 사용
    valid_sources = {"thalamus", "exact_cache", "semantic_cache", "tier_1_5", "swarm", "fallback"}
    # 'continuation_bypass' / 'continuation_swarm' 같은 신규 값 추가 금지
    forbidden = ["continuation_bypass", "continuation_swarm", "forced_swarm"]
    for token in forbidden:
        assert f'response_source="{token}"' not in source
        assert f"response_source='{token}'" not in source


# ---------------------------------------------------------------------------
# SwarmTrace schema 변경 0건
# ---------------------------------------------------------------------------


def test_swarm_trace_schema_unchanged():
    """SwarmTrace에 continuation 관련 신규 필드가 추가되지 않음."""
    response_py = Path("app/api/schemas/response.py")
    source = response_py.read_text(encoding="utf-8")
    assert "continuation" not in source.lower()


# ---------------------------------------------------------------------------
# PFC.infer_hint() 시그니처 변경 0건
# ---------------------------------------------------------------------------


def test_pfc_infer_hint_signature_unchanged():
    """infer_hint(query, eval_result, goal_stack_summary, active_goal) 시그니처."""
    source = _PFC_PY.read_text(encoding="utf-8")
    # async def infer_hint( 가 존재해야 한다
    assert "async def infer_hint(" in source
    # 기존 4종 파라미터가 모두 보존됨
    assert "query: str" in source
    assert "eval_result: EvaluationResult" in source
    assert "goal_stack_summary:" in source
    assert "active_goal:" in source

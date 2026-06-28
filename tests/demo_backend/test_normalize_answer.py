"""demo_backend AnswerView 정직화 + public live-only 정책 테스트.

하드 규칙: mock answer 텍스트는 public demo에 노출하지 않는다.
early-exit는 reflex/cache/safety_blocked로 구분(stub로 뭉치지 않음).
DEMO_REQUIRE_LIVE=true면 비-live answer를 "Live mode unavailable"로 차단.
"""
from __future__ import annotations

from demo_backend.main import _build_answer_view, _normalize


def _swarm(answer, *, llm_mode, answer_source, model="gpt-x"):
    return {
        "response_source": "swarm",
        "answer": answer,
        "answer_source": answer_source,
        "llm_mode": llm_mode,
        "swarm_trace": {
            "executed": True, "status": "ok", "generator_model_name": model,
        },
    }


# ── swarm 경로 ──────────────────────────────────────────────────────────────
def test_live_generator_answer_is_shown():
    av = _build_answer_view(
        _swarm("real live answer", llm_mode="live", answer_source="generator"),
        require_live=False,
    )
    assert av.text == "real live answer"
    assert av.mode == "live"
    assert av.gated is False
    assert av.source == "live_generator"


def test_mock_generator_answer_is_hidden():
    av = _build_answer_view(
        _swarm("[MOCK STANDARD] secret prompt echo", llm_mode="mock",
               answer_source="generator"),
        require_live=False,
    )
    # mock answer 텍스트는 절대 노출하지 않는다.
    assert av.text == ""
    assert av.mode == "stub"
    assert av.gated is True
    assert av.source == "mock_hidden"


def test_unavailable_answer_is_hidden():
    av = _build_answer_view(
        _swarm("[ANSWER UNAVAILABLE] generation unavailable", llm_mode="live",
               answer_source="unavailable"),
        require_live=False,
    )
    assert av.text == ""
    assert av.source == "unavailable"
    assert av.gated is True


# ── 비-swarm 결정론적/시스템 응답: 성질별 라벨 ──────────────────────────────
def test_thalamus_is_reflex_not_stub():
    av = _build_answer_view(
        {"response_source": "thalamus", "answer": "Hello!"}, require_live=False,
    )
    assert av.text == "Hello!"
    assert av.source == "reflex"
    assert av.gated is False


def test_cache_is_labeled_cache():
    av = _build_answer_view(
        {"response_source": "semantic_cache", "answer": "cached"}, require_live=False,
    )
    assert av.source == "cache"
    assert av.gated is False


def test_glycine_fallback_is_safety_blocked():
    av = _build_answer_view(
        {"response_source": "fallback", "answer": "[GLYCINE BLOCKED] rate"},
        require_live=False,
    )
    assert av.source == "safety_blocked"
    assert av.gated is True


# ── DEMO_REQUIRE_LIVE gate ──────────────────────────────────────────────────
def test_require_live_blocks_non_live():
    av = _build_answer_view(
        _swarm("[MOCK ...]", llm_mode="mock", answer_source="generator"),
        require_live=True,
    )
    assert av.text == "Live mode unavailable"
    assert av.source == "live_required"
    assert av.gated is True


def test_require_live_still_shows_live():
    av = _build_answer_view(
        _swarm("real", llm_mode="live", answer_source="generator"),
        require_live=True,
    )
    assert av.text == "real"
    assert av.mode == "live"


# ── _normalize 통합: safety_invariants + generator_model_name ───────────────
def test_normalize_reflects_live_and_model_name():
    qd = _swarm("real live", llm_mode="live", answer_source="generator", model="m-live")
    result = _normalize(qd, run_id="r1", session_id="s1", trace_data=None,
                        require_live=False)
    assert result.safety_invariants.llm_live_enabled is True
    assert result.cortex.answer.text == "real live"
    assert result.cortex.swarm_trace.generator_model_name == "m-live"


def test_normalize_mock_hides_answer_and_marks_off():
    qd = _swarm("[MOCK ...]", llm_mode="mock", answer_source="generator")
    result = _normalize(qd, run_id="r2", session_id="s2", trace_data=None,
                        require_live=False)
    assert result.safety_invariants.llm_live_enabled is False
    assert result.cortex.answer.text == ""
    assert result.cortex.answer.source == "mock_hidden"

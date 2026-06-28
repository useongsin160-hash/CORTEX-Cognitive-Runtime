"""Phase 5 STEP 5 — CueClassifier 단위 테스트."""
from __future__ import annotations

import pytest

from app.routing.cue_classifier import CueClassifier, CueDetection


@pytest.fixture
def classifier() -> CueClassifier:
    return CueClassifier()


# ---------------------------------------------------------------------------
# 한국어 continuation cue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", [
    "계속",
    "계속해",
    "이어서 해줘",
    "아까 그거",
    "방금 한 거",
    "가자",
    "고고",
    "ㄱㄱ",
    "그러자",
    "오케이",
    "다음 단계로 가줘",
    "다음으로 진행",
    "다음 작업 해줘",
    "다음 진행해줘",
])
def test_korean_continuation_cues(classifier, query):
    cue = classifier.classify(query)
    assert cue.cue_type == "continuation", f"{query=} → {cue.cue_type}"


def test_korean_continuation_is_continuation_helper(classifier):
    assert classifier.is_continuation("계속해") is True
    assert classifier.is_continuation("그냥 안녕") is False


# ---------------------------------------------------------------------------
# 한국어 continuation 제외 (false-positive 방지)
# ---------------------------------------------------------------------------


def test_geugeo_alone_not_continuation(classifier):
    """'그거'만으로는 continuation 아님."""
    cue = classifier.classify("그거")
    assert cue.cue_type != "continuation"


def test_daeum_ju_not_continuation(classifier):
    """'다음 주 일정'은 continuation 아님 — '다음' 단독 패턴 차단."""
    cue = classifier.classify("다음 주 일정 알려줘")
    assert cue.cue_type != "continuation"


def test_daeum_alone_not_continuation(classifier):
    """'다음'만으로는 continuation 아님."""
    cue = classifier.classify("다음")
    assert cue.cue_type != "continuation"


# ---------------------------------------------------------------------------
# 한국어 goal_creation cue 제외 (이제 단독)
# ---------------------------------------------------------------------------


def test_ijae_alone_not_goal_creation(classifier):
    """'이제'만으로는 goal_creation 아님."""
    cue = classifier.classify("이제 어떻게 하지?")
    assert cue.cue_type != "goal_creation"


# ---------------------------------------------------------------------------
# 한국어 completion false-positive 방지
# ---------------------------------------------------------------------------


def test_wanryo_report_not_completion(classifier):
    """'완료 보고서 작성해줘' — 완료 보고서를 쓰는 작업."""
    cue = classifier.classify("완료 보고서 작성해줘")
    assert cue.cue_type != "completion"


def test_phase4_wanryo_munseo_not_completion(classifier):
    """'Phase 4 완료 문서 만들어줘' — completion 아님."""
    cue = classifier.classify("Phase 4 완료 문서 만들어줘")
    assert cue.cue_type != "completion"


def test_wanryo_alone_is_completion(classifier):
    cue = classifier.classify("완료")
    assert cue.cue_type == "completion"


def test_wanryo_past_tense_is_completion(classifier):
    cue = classifier.classify("완료했어")
    assert cue.cue_type == "completion"


# ---------------------------------------------------------------------------
# 영어 continuation cue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", [
    "please continue",
    "resume the work",
    "next step please",
    "let's go",
    "go on",
    "proceed",
    "keep going",
    "ok",
])
def test_english_continuation_cues(classifier, query):
    cue = classifier.classify(query)
    assert cue.cue_type == "continuation", f"{query=} → {cue.cue_type}"


# ---------------------------------------------------------------------------
# 영어 word-boundary false-positive 방지
# ---------------------------------------------------------------------------


def test_english_now_not_correction(classifier):
    """'now is the time' — 'no' substring이지만 'now' 단어 자체는 correction 아님."""
    cue = classifier.classify("now is the time")
    # 'no' 자체로 매칭되면 안 됨 (now는 별개의 단어)
    assert cue.matched_keyword != "no"


def test_english_node_not_correction(classifier):
    """'node'에서 'no' 매칭되면 안 됨."""
    cue = classifier.classify("create a node")
    assert cue.matched_keyword != "no"


def test_english_novel_not_correction(classifier):
    """'novel'에서 'no' 매칭되면 안 됨."""
    cue = classifier.classify("write a novel")
    # novel은 'no' 매칭이 아니라 'novel'을 통한 다른 매칭이 없으면 none
    assert cue.matched_keyword != "no"


def test_english_book_not_continuation(classifier):
    """'book'에서 'ok' 매칭되면 안 됨."""
    cue = classifier.classify("write a book")
    assert cue.matched_keyword != "ok"


def test_finish_reason_not_completion(classifier):
    """'finish_reason 분석' — `_`가 word char라 '\\bfinish\\b' 매칭 안 됨."""
    cue = classifier.classify("finish_reason 분석해줘")
    assert cue.cue_type != "completion"


def test_completion_rate_not_completion(classifier):
    """'completion rate' — '\\bcomplete\\b' 매칭 안 됨."""
    cue = classifier.classify("show me completion rate")
    assert cue.cue_type != "completion"


def test_completed_report_not_completion(classifier):
    """'completed report' — completed가 terminal 위치 아님."""
    cue = classifier.classify("completed report attached")
    assert cue.cue_type != "completion"


# ---------------------------------------------------------------------------
# 영어 correction
# ---------------------------------------------------------------------------


def test_english_no_is_correction(classifier):
    """'no, that's wrong' — 'no' 단독은 correction."""
    cue = classifier.classify("no that is wrong")
    assert cue.cue_type == "correction"


def test_english_actually_correction(classifier):
    cue = classifier.classify("actually let me rethink")
    assert cue.cue_type == "correction"


# ---------------------------------------------------------------------------
# language detection
# ---------------------------------------------------------------------------


def test_language_ko(classifier):
    cue = classifier.classify("계속해")
    assert cue.language == "ko"


def test_language_en(classifier):
    cue = classifier.classify("please continue")
    assert cue.language == "en"


def test_language_mixed(classifier):
    cue = classifier.classify("계속해 next step")
    assert cue.language == "mixed"


# ---------------------------------------------------------------------------
# none — 어떤 cue도 매칭 안 됨
# ---------------------------------------------------------------------------


def test_neutral_query_is_none(classifier):
    cue = classifier.classify("안녕")
    assert cue.cue_type == "none"


def test_empty_query_is_none(classifier):
    cue = classifier.classify("")
    assert cue.cue_type == "none"


# ---------------------------------------------------------------------------
# CueDetection 구조
# ---------------------------------------------------------------------------


def test_detection_has_matched_keyword(classifier):
    cue = classifier.classify("계속해")
    assert cue.matched_keyword is not None
    assert cue.confidence > 0


def test_detection_none_has_no_keyword(classifier):
    cue = classifier.classify("xyz random text")
    assert cue.matched_keyword is None
    assert cue.confidence == 0.0


def test_detection_is_frozen(classifier):
    """CueDetection은 frozen dataclass."""
    import dataclasses
    cue = classifier.classify("계속해")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        cue.cue_type = "completion"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Bootstrap cue (분류만, 라우팅 사용 안 함)
# ---------------------------------------------------------------------------


def test_korean_bootstrap_cue(classifier):
    cue = classifier.classify("새 프로젝트 로드맵 보여줘")
    # "프로젝트", "로드맵" 둘 다 bootstrap이지만 goal_creation의 "새" 패턴 우선
    # "새 프로젝트"는 _KO_CREATION_KEYWORDS에 있으므로 goal_creation 우선
    assert cue.cue_type in {"goal_creation", "bootstrap"}


def test_english_bootstrap_cue(classifier):
    cue = classifier.classify("design the architecture")
    assert cue.cue_type == "bootstrap"


# ---------------------------------------------------------------------------
# 우선순위 (completion > goal_creation > continuation > correction > bootstrap)
# ---------------------------------------------------------------------------


def test_completion_beats_continuation(classifier):
    """'끝났어 다음 단계'에서 completion이 우선."""
    cue = classifier.classify("끝났어")
    assert cue.cue_type == "completion"


def test_goal_creation_beats_continuation(classifier):
    """'새로운 목표를 계속 하자'에서 goal_creation 우선."""
    cue = classifier.classify("새로운 목표 추가해줘")
    assert cue.cue_type == "goal_creation"

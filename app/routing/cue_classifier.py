"""Phase 5 STEP 5 — CueClassifier: 한/영 cue 통합 분류.

PFC와 ContinuationDetector가 공유하는 단일 cue 탐지 모듈.

언어 정책: 이번 STEP은 한국어 / 영어만 지원한다.
임베딩 기반 cue 분류는 Phase 6 이후 검토.

False-positive 방지:
- "그거"는 continuation cue에서 제외
- raw "다음" 단독은 continuation cue에서 제외 ("다음 단계", "다음으로",
  "다음 작업", "다음 진행" 등 명확한 결합만 허용)
- "이제 " 단독은 goal_creation cue에서 제외
- "완료 보고서 작성", "completion rate", "finish_reason"은 completion 아님
- "node", "novel", "now", "book" 같은 부분 일치 0건 — \\b word boundary 사용
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

CueType = Literal[
    "completion",
    "goal_creation",
    "continuation",
    "correction",
    "bootstrap",
    "none",
]

Language = Literal["ko", "en", "mixed"]


@dataclass(frozen=True)
class CueDetection:
    """단일 cue 탐지 결과 — 순수 dataclass, lock/coroutine/store 객체 포함 금지."""

    cue_type: CueType
    language: Language
    matched_keyword: str | None
    confidence: float


# ---------------------------------------------------------------------------
# Korean completion patterns — 종결 위치 또는 명시적 과거형만 허용
# ---------------------------------------------------------------------------

_KO_COMPLETION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    r"완료\s*[.!?]*\s*$"                                # "완료" / "완료!" 종결
    r"|완료\s*(?:했어|됐어|했습니다|됩니다|했다|됐다)"   # "완료 + 과거형"
    r"|끝났(?:어|다|습니다|어요|네요|음)?(?:\b|$)"        # "끝났" + (어/다/음/...)
    r"|다\s*했어"
    r"|마쳤(?:어|다|습니다)?"
    r"|마침\s*[.!?]*\s*$"
    r"|그만\s*[.!?]*\s*$"
    r"|그만\s*(?:했어|하자|할게|할래)"
    r"|여기까지\s*[.!?]*\s*$"
    r"|종료\s*[.!?]*\s*$"
    r")"
)

# English completion: 종결 위치만 허용 → "finish_reason", "completion rate",
# "completed report" 등 후속 토큰 있는 경우 false-positive 차단
_EN_COMPLETION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\s)(?:done|finished|completed|finish|stop|complete|stopped)"
    r"\s*[.!?]*\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Korean goal_creation
# ---------------------------------------------------------------------------

_KO_CREATION_KEYWORDS: Final[frozenset[str]] = frozenset({
    # 기존 STEP 3 키워드 (회귀 보존)
    "목표", "목적", "만들어줘", "추가해줘", "새로운",
    "시작하자", "시작할게", "시작해",
    "하고 싶어", "하고싶어", "하려고", "하려 해", "하려는", "원해",
    # STEP 5 추가
    "새로 시작", "새 프로젝트", "새 목표", "넘어가자",
})

# "이제" 단독 → goal_creation 아님. "이제 X 시작/하자/보자" 같은 명시적 결합만.
# 기존 _KO_CREATION_KEYWORDS와 OR 결합되며 "이제" 단독 false-positive 방지.

_EN_CREATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:start|begin|new project|new goal|new task"
    r"|goal|objective"
    r"|want to|would like to)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Korean continuation — 정확한 결합 패턴 + 제한된 키워드 집합
# ---------------------------------------------------------------------------

# "다음 단계", "다음으로", "다음 작업", "다음 진행" 등 명시적 결합만 허용
_KO_CONTINUATION_DAEUM_RE: Final[re.Pattern[str]] = re.compile(
    r"다음\s*(?:단계|으로|작업|진행|페이즈|스텝)"
)

# 강한 cue 키워드 — "다음" 단독 / "그거" 제외
_KO_CONTINUATION_KEYWORDS: Final[frozenset[str]] = frozenset({
    "계속", "이어서", "이어", "그다음",
    "아까", "방금",
    "가자", "고고", "ㄱㄱ", "그러자", "오케이",
    "더",  # 기존 _CONTINUATION_KEYWORDS 회귀 보존
})

_EN_CONTINUATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:continue|resume|next step|next|ok|let'?s go|go on|proceed|keep going)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Korean correction
# ---------------------------------------------------------------------------

_KO_CORRECTION_KEYWORDS: Final[frozenset[str]] = frozenset({
    # 기존 STEP 3 키워드 (회귀 보존)
    "수정", "바꿔", "변경", "틀렸어", "아니야", "아니고",
    # STEP 5 추가
    "그게 아니라", "고쳐", "다시 해", "방금 거 기준",
})

_EN_CORRECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:no|actually|wait|correct|fix|change|wrong|mistake)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Bootstrap (분류만 — STEP 5에서 라우팅 사용 안 함)
# ---------------------------------------------------------------------------

_KO_BOOTSTRAP_KEYWORDS: Final[frozenset[str]] = frozenset({
    "프로젝트", "설계", "페이즈", "구현", "로드맵",
})

_EN_BOOTSTRAP_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:project|design|implement|roadmap)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_HANGUL_RE: Final[re.Pattern[str]] = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_ENGLISH_LETTER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]")


def _detect_language(query: str) -> Language:
    has_ko = bool(_HANGUL_RE.search(query))
    has_en = bool(_ENGLISH_LETTER_RE.search(query))
    if has_ko and has_en:
        return "mixed"
    if has_ko:
        return "ko"
    return "en"


def _find_substring(text: str, keywords: frozenset[str]) -> str | None:
    for kw in keywords:
        if kw in text:
            return kw
    return None


# Korean confidence — PFC 회귀 보존을 위해 cue별 confidence 고정
_KO_CUE_CONFIDENCE: Final[dict[str, float]] = {
    "completion": 0.9,
    "goal_creation": 0.8,
    "continuation": 0.85,
    "correction": 0.75,
    "bootstrap": 0.5,
}


# ---------------------------------------------------------------------------
# CueClassifier
# ---------------------------------------------------------------------------


class CueClassifier:
    """Stateless cue classifier.

    제약:
    - LLM / embedder 사용 금지
    - 외부 store / 네트워크 접근 금지
    - 한/영 외 언어는 미지원 (Phase 5 closeout 또는 별도 ADR 부채로 명시)
    """

    def classify(self, query: str) -> CueDetection:
        """Top-priority cue 1개 반환. 우선순위는 PFC 8단계 사다리와 일치.

        순서:
          1. completion (한/영)
          2. goal_creation (한/영)
          3. continuation (한/영)
          4. correction (한/영)
          5. bootstrap (한/영)
          6. none
        """
        language = _detect_language(query)

        # 1. completion
        m = _KO_COMPLETION_RE.search(query)
        if m:
            return CueDetection(
                cue_type="completion",
                language=language,
                matched_keyword=m.group(0).strip(),
                confidence=_KO_CUE_CONFIDENCE["completion"],
            )
        m = _EN_COMPLETION_RE.search(query)
        if m:
            return CueDetection(
                cue_type="completion",
                language=language,
                matched_keyword=m.group(0).strip(),
                confidence=_KO_CUE_CONFIDENCE["completion"],
            )

        # 2. goal_creation
        kw = _find_substring(query, _KO_CREATION_KEYWORDS)
        if kw:
            return CueDetection(
                cue_type="goal_creation",
                language=language,
                matched_keyword=kw,
                confidence=_KO_CUE_CONFIDENCE["goal_creation"],
            )
        m = _EN_CREATION_RE.search(query)
        if m:
            return CueDetection(
                cue_type="goal_creation",
                language=language,
                matched_keyword=m.group(0),
                confidence=_KO_CUE_CONFIDENCE["goal_creation"],
            )

        # 3. continuation
        m = _KO_CONTINUATION_DAEUM_RE.search(query)
        if m:
            return CueDetection(
                cue_type="continuation",
                language=language,
                matched_keyword=m.group(0),
                confidence=_KO_CUE_CONFIDENCE["continuation"],
            )
        kw = _find_substring(query, _KO_CONTINUATION_KEYWORDS)
        if kw:
            return CueDetection(
                cue_type="continuation",
                language=language,
                matched_keyword=kw,
                confidence=_KO_CUE_CONFIDENCE["continuation"],
            )
        m = _EN_CONTINUATION_RE.search(query)
        if m:
            return CueDetection(
                cue_type="continuation",
                language=language,
                matched_keyword=m.group(0).lower(),
                confidence=_KO_CUE_CONFIDENCE["continuation"],
            )

        # 4. correction
        kw = _find_substring(query, _KO_CORRECTION_KEYWORDS)
        if kw:
            return CueDetection(
                cue_type="correction",
                language=language,
                matched_keyword=kw,
                confidence=_KO_CUE_CONFIDENCE["correction"],
            )
        m = _EN_CORRECTION_RE.search(query)
        if m:
            return CueDetection(
                cue_type="correction",
                language=language,
                matched_keyword=m.group(0).lower(),
                confidence=_KO_CUE_CONFIDENCE["correction"],
            )

        # 5. bootstrap
        kw = _find_substring(query, _KO_BOOTSTRAP_KEYWORDS)
        if kw:
            return CueDetection(
                cue_type="bootstrap",
                language=language,
                matched_keyword=kw,
                confidence=_KO_CUE_CONFIDENCE["bootstrap"],
            )
        m = _EN_BOOTSTRAP_RE.search(query)
        if m:
            return CueDetection(
                cue_type="bootstrap",
                language=language,
                matched_keyword=m.group(0).lower(),
                confidence=_KO_CUE_CONFIDENCE["bootstrap"],
            )

        return CueDetection(
            cue_type="none",
            language=language,
            matched_keyword=None,
            confidence=0.0,
        )

    def is_continuation(self, query: str) -> bool:
        """Convenience method — continuation cue 여부."""
        return self.classify(query).cue_type == "continuation"

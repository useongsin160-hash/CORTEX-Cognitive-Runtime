"""Layer 2 — Neuromodulators (에피네프린 / 글리신).

설계 문서 "Neuromodulators (에피네프린 / 글리신 병렬 연산)" 구현.
이 파일은 신규 작성 — 이전 phase에는 동등한 모듈이 없었다.

에피네프린:
  - 발동 조건: 고난이도 연산 감지 (= HIGH_COMPUTE 카테고리 + 충분한
    분류 신뢰도)
  - 효과: Pro 모델 체급으로 전환 (ModelTier 상향)

게이트 처리 순서 (STEP 3.2 보정 1):
  1. category가 매핑에 없음 → "unknown_category"
  2. category가 HIGH가 아님 → "category_gate_fail"
  3. similarity가 threshold 미달 → "similarity_gate_fail"
  4. 두 게이트 통과 → "activated"
"""
from __future__ import annotations

import dataclasses
import hashlib
import time

from app.core.config import EpinephrineConfig
from app.core.model_tier import ModelTier
from app.execution.params import GenerationParams

# Norepinephrine 적용 시 파라미터 변조 기준값.
NE_TEMPERATURE_CEILING = 0.1  # 설계서 line 330
NE_TOP_K_FLOOR = 80           # 일반 40의 2배

# Public reason codes. Tests assert against these literals.
REASON_UNKNOWN_CATEGORY = "unknown_category"
REASON_CATEGORY_GATE_FAIL = "category_gate_fail"
REASON_SIMILARITY_GATE_FAIL = "similarity_gate_fail"
REASON_ACTIVATED = "activated"


class Epinephrine:
    """에피네프린: 고난이도 연산 감지 → 모델 체급 전환.

    발동 조건 (AND):
      1. 카테고리 게이트: category ∈ HIGH_COMPUTE_CATEGORIES
      2. 신뢰도 게이트: self_similarity >= threshold

    similarity는 mean-centered cosine similarity (-1.0 ~ 1.0).
    """

    def __init__(self, config: EpinephrineConfig) -> None:
        self._config = config

    async def decide(
        self,
        category: str,
        similarity: float,
    ) -> tuple[bool, ModelTier, str]:
        """Run the gate cascade and return (activated, tier, reason).

        reason values:
          - "unknown_category"      : category not registered in
            category_tier_map (e.g. evaluator returned an unrecognized
            label). Tier defaults to STANDARD.
          - "category_gate_fail"    : category is LOW_COMPUTE — drop to
            the category's default tier (no Epinephrine boost).
          - "similarity_gate_fail"  : HIGH category but classifier
            confidence below threshold → STANDARD (don't trust the
            label enough to commit to a heavy tier).
          - "activated"             : both gates passed → use the
            HIGH-tier mapping from category_tier_map.
        """
        # Gate 1: unknown category — check FIRST (before HIGH membership).
        if category not in self._config.category_tier_map:
            return False, ModelTier.STANDARD, REASON_UNKNOWN_CATEGORY

        default_tier = self._config.category_tier_map[category]

        # Gate 2: category bucket gate.
        if category not in self._config.high_compute_categories:
            return False, default_tier, REASON_CATEGORY_GATE_FAIL

        # Gate 3: confidence gate. similarity ∈ [-1, 1]; threshold is in
        # the same mean-centered frame as the centroids.
        if similarity < self._config.similarity_threshold:
            return False, ModelTier.STANDARD, REASON_SIMILARITY_GATE_FAIL

        return True, default_tier, REASON_ACTIVATED


# PHASE 3.5: Synapse 통합 시 game_design + HARD 같은 복합 패턴 처리
# PHASE 6: Dopamine RPE 도입 시 threshold 동적 조정


class Norepinephrine:
    """노르에피네프린: 일시적 감각 증폭기.

    설계서 line 330 (B12 5단계 정합):
      - 발동 조건: LC가 난이도 高 판정 (difficulty >= 4 = VERY_HARD)
      - 효과: top_k 확장, temperature 0.1 이하 고정
      - 범위: 해당 Task 생명 주기 내에서만 유지

    발동 기준과 적용 조건이 분리된다:
      - 발동 기준: difficulty >= 4 (5단계에서 3은 중간 — 고난도 임계를 4로 상향)
      - 실제 적용: ne_active AND tier >= STANDARD
        (난이도→tier 1:1이라 difficulty>=4 → tier HEAVY/DEEP >= STANDARD,
         즉 발동 시 tier_mismatch 는 구조적으로 발생하지 않는다)
      - mismatch (ne_active + tier < STANDARD): 변조 없음, 로그만
    """

    async def should_activate(self, difficulty: int) -> bool:
        """발동 여부 결정. B12 5단계 — difficulty >= 4 (VERY_HARD 이상)."""
        return difficulty >= 4

    async def modify_params(
        self,
        params: GenerationParams,
        tier: ModelTier,
        ne_active: bool,
    ) -> GenerationParams:
        """ne_active이고 tier >= STANDARD이면 파라미터 변조.

        변조:
          - temperature = min(현재값, NE_TEMPERATURE_CEILING)
          - top_k = max(현재값, NE_TOP_K_FLOOR)

        tier가 STANDARD 미만인데 ne_active=True면 mismatch — 변조하지
        않고 ne_applied=False / ne_reason="tier_mismatch"로 표시.
        """
        if not ne_active:
            return params.model_copy(update={
                "ne_applied": False,
                "ne_reason": None,
            })

        if tier < ModelTier.STANDARD:
            # mismatch: 발동했지만 tier가 낮음 — 강제 승격 금지, 로그만.
            return params.model_copy(update={
                "ne_applied": False,
                "ne_reason": "tier_mismatch",
            })

        return params.model_copy(update={
            "temperature": min(params.temperature, NE_TEMPERATURE_CEILING),
            "top_k": max(params.top_k, NE_TOP_K_FLOOR),
            "ne_applied": True,
            "ne_reason": "high_difficulty",  # B12: difficulty >= VERY_HARD(4)
        })


@dataclasses.dataclass(frozen=True)
class GlycineConfig:
    token_budget: int = 4000
    rate_window_seconds: float = 60.0
    rate_max_requests: int = 30
    loop_threshold: int = 5
    loop_window_seconds: float = 60.0


@dataclasses.dataclass
class GlycineDecision:
    active: bool
    reason: str | None = None
    action: str | None = None


@dataclasses.dataclass
class _SessionState:
    request_timestamps: list[float] = dataclasses.field(default_factory=list)
    prompt_history: list[tuple[str, float]] = dataclasses.field(default_factory=list)


class Glycine:
    """글리신 (억제): pre-flight hard brake.

    세 가지 가드 (순서대로 평가, 첫 실패에서 즉시 차단):
      1. token_budget  : len(prompt) // 4 >= token_budget
      2. rate_limit    : session 내 rate_window_seconds 이내 요청 수 >= rate_max_requests
      3. loop_guard    : loop_window_seconds 이내 동일 prompt hash 반복 횟수 + 1 >= loop_threshold

    기록 (request_timestamps / prompt_history)은 통과한 요청에만 남긴다.
    """

    def __init__(self, config: GlycineConfig | None = None) -> None:
        self._config = config or GlycineConfig()
        self._sessions: dict[str, _SessionState] = {}

    def _session(self, key: str) -> _SessionState:
        if key not in self._sessions:
            self._sessions[key] = _SessionState()
        return self._sessions[key]

    async def check_pre_flight(self, prompt: str, session_key: str) -> GlycineDecision:
        cfg = self._config
        now = time.monotonic()

        # Guard 1: token budget (4 chars ≈ 1 token)
        estimated_tokens = len(prompt) // 4
        if estimated_tokens >= cfg.token_budget:
            return GlycineDecision(
                active=True,
                reason=f"token_budget_exceeded: estimated {estimated_tokens} tokens >= {cfg.token_budget}",
                action="block",
            )

        state = self._session(session_key)

        # Guard 2: rate limit
        window_start = now - cfg.rate_window_seconds
        recent = [t for t in state.request_timestamps if t >= window_start]
        if len(recent) >= cfg.rate_max_requests:
            return GlycineDecision(
                active=True,
                reason=f"rate_limit_exceeded: {len(recent)} requests in {cfg.rate_window_seconds}s window",
                action="block",
            )

        # Guard 3: loop guard
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]  # noqa: S324
        loop_start = now - cfg.loop_window_seconds
        same_count = sum(
            1 for h, t in state.prompt_history
            if h == prompt_hash and t >= loop_start
        )
        if same_count + 1 >= cfg.loop_threshold:
            return GlycineDecision(
                active=True,
                reason=f"loop_detected: same prompt repeated {same_count + 1} times in {cfg.loop_window_seconds}s",
                action="block",
            )

        # All guards passed — record this request
        state.request_timestamps.append(now)
        state.prompt_history.append((prompt_hash, now))

        return GlycineDecision(active=False)

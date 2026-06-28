"""인메모리 세션 rate limit (단일 프로세스 가정).

동기 메서드만 — asyncio 이벤트루프 내에서는 await 없이 호출되므로 check()는 원자적이다.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    scope: str | None = None       # "per_minute" | "per_session" | "global"
    limit: int | None = None
    retry_after: int | None = None  # 초 (per_minute 일 때만)


class InMemoryRateLimiter:
    """분당(세션별 슬라이딩 윈도우) / 세션 누적 / 전역 누적 3중 제한."""

    def __init__(
        self,
        *,
        per_minute: int = 10,
        per_session: int = 50,
        global_total: int = 500,
        window_seconds: float = 60.0,
    ) -> None:
        self._per_minute = per_minute
        self._per_session = per_session
        self._global_total = global_total
        self._window = window_seconds
        self._minute_hits: dict[str, deque[float]] = {}
        self._session_totals: dict[str, int] = {}
        self._global_count = 0

    def check(self, session_id: str, *, now: float | None = None) -> RateLimitResult:
        now = time.monotonic() if now is None else now

        # 전역 누적
        if self._global_count >= self._global_total:
            return RateLimitResult(False, scope="global", limit=self._global_total)

        # 세션 누적
        if self._session_totals.get(session_id, 0) >= self._per_session:
            return RateLimitResult(False, scope="per_session", limit=self._per_session)

        # 분당 슬라이딩 윈도우
        hits = self._minute_hits.setdefault(session_id, deque())
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._per_minute:
            retry_after = max(1, int(hits[0] + self._window - now) + 1)
            return RateLimitResult(
                False, scope="per_minute", limit=self._per_minute, retry_after=retry_after
            )

        # 허용 — 카운터 증가
        hits.append(now)
        self._session_totals[session_id] = self._session_totals.get(session_id, 0) + 1
        self._global_count += 1
        return RateLimitResult(True)

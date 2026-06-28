"""CORTEX core(8000) httpx 프록시 클라이언트.

CORTEX 를 직접 import 하지 않는다 — HTTP 만 호출한다. 로깅에 prompt 본문이나 키를
남기지 않는다(키는 CORTEX 가 다루므로 데모는 취급조차 안 함).
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("demo_backend.cortex_client")


class CortexUnreachableError(Exception):
    """CORTEX 연결 불가(연결 거부/타임아웃) — /demo/chat 에서 503 게이트로 매핑."""


class CortexResponseError(Exception):
    """CORTEX 가 비2xx 응답 — /demo/chat 에서 502 로 매핑."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"CORTEX returned HTTP {status_code}")
        self.status_code = status_code


class CortexClient:
    def __init__(
        self,
        base_url: str,
        *,
        query_timeout: float = 30.0,
        health_timeout: float = 3.0,
        trace_timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport)
        self._query_timeout = query_timeout
        self._health_timeout = health_timeout
        self._trace_timeout = trace_timeout

    async def health(self) -> dict | None:
        """GET /health. 어떤 실패든 None 반환(readiness 의 cortex_reachable 판정용)."""
        try:
            resp = await self._client.get("/health", timeout=self._health_timeout)
            if resp.status_code == 200:
                return resp.json()
            return None
        except httpx.HTTPError:
            return None

    async def query(self, message: str, session_id: str) -> dict:
        """POST /query. demo message→CORTEX prompt 매핑.

        연결 불가 → CortexUnreachableError, 비2xx → CortexResponseError.
        """
        payload = {"prompt": message, "session_id": session_id}
        try:
            resp = await self._client.post(
                "/query", json=payload, timeout=self._query_timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("CORTEX /query unreachable: %s", type(exc).__name__)
            raise CortexUnreachableError("CORTEX /query is unreachable") from None

        if resp.status_code // 100 != 2:
            logger.warning("CORTEX /query status=%s", resp.status_code)
            raise CortexResponseError(resp.status_code)
        return resp.json()

    async def get_trace(self, trace_id: str) -> dict | None:
        """GET /trace/{id}. best-effort — 어떤 실패든 None(절대 raise 안 함)."""
        try:
            resp = await self._client.get(
                f"/trace/{trace_id}", timeout=self._trace_timeout
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except httpx.HTTPError:
            return None

    async def aclose(self) -> None:
        await self._client.aclose()

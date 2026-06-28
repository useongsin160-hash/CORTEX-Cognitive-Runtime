"""demo_backend 설정 — 환경변수 기반.

CORTEX core 의 app/core/config.py 와 독립적이다(데모는 HTTP 프록시라 슬롯/키를 직접
취급하지 않는다). API 키/슬롯 상태는 이 설정이 다루지 않는다 — readiness 는 core
/health 의 slots_ready(벤더 중립 집계)를 그대로 중계한다.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DemoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    # CORTEX core 위치 (서버→서버 httpx).
    cortex_url: str = Field(default="http://127.0.0.1:8000", alias="CORTEX_URL")

    # 바인딩 — 분리 배포라도 데모 서버는 로컬/리버스프록시 뒤를 가정해 127.0.0.1 기본.
    host: str = Field(default="127.0.0.1", alias="DEMO_HOST")
    port: int = Field(default=8001, alias="DEMO_PORT")

    # cross-origin 화이트리스트 (쉼표구분). 와일드카드 미사용.
    # pydantic-settings 는 list[str] env 를 JSON 으로 파싱하려 하므로 str 로 받고
    # allowed_origins property 에서 split/trim 한다.
    allowed_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="DEMO_ALLOWED_ORIGINS",
    )

    # public demo live-only gate. 기본 false → 기존 동작 보존.
    # true 면 live(answer_source=generator & llm_mode=live)가 아닌 모든 run의 answer를
    # "Live mode unavailable"로 차단한다. (telemetry/trace는 항상 그대로 노출.)
    # MockLLMClient answer는 설정과 무관하게 public demo에서 절대 노출하지 않는다.
    require_live: bool = Field(default=False, alias="DEMO_REQUIRE_LIVE")

    # 요청 본문 크기 상한.
    max_request_bytes: int = Field(default=16384, alias="DEMO_MAX_REQUEST_BYTES")

    # 인메모리 rate limit.
    rate_per_minute: int = Field(default=10, alias="DEMO_RATE_PER_MINUTE")
    rate_per_session: int = Field(default=50, alias="DEMO_RATE_PER_SESSION")
    rate_global: int = Field(default=500, alias="DEMO_RATE_GLOBAL")

    # CORTEX 호출 타임아웃(초).
    cortex_query_timeout: float = Field(default=30.0, alias="DEMO_CORTEX_QUERY_TIMEOUT")
    cortex_health_timeout: float = Field(default=3.0, alias="DEMO_CORTEX_HEALTH_TIMEOUT")
    cortex_trace_timeout: float = Field(default=5.0, alias="DEMO_CORTEX_TRACE_TIMEOUT")

    @computed_field
    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_demo_settings() -> DemoSettings:
    return DemoSettings()

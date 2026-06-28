"""LLM 호출 파라미터 모델."""
from __future__ import annotations

from pydantic import BaseModel, Field


class GenerationParams(BaseModel):
    """LLM 호출 파라미터.

    Norepinephrine 활성화 시 일부 필드가 LC 경로에서 변조된다.
    설계서 line 330: top_k 확장, temperature 0.1 이하 고정.
    """

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_k: int = Field(default=40, ge=1, le=200)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    max_tokens: int = Field(default=2048, ge=1, le=32000)

    # 디버깅/관측용
    ne_applied: bool = Field(default=False)
    ne_reason: str | None = Field(default=None)

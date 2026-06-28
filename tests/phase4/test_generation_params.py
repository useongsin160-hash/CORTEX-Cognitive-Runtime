"""Phase 4 STEP 1 — GenerationParams."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.execution.params import GenerationParams


def test_default_values():
    p = GenerationParams()
    assert p.temperature == 0.7
    assert p.top_k == 40
    assert p.top_p == 0.9
    assert p.max_tokens == 2048


def test_ne_fields_default_unset():
    p = GenerationParams()
    assert p.ne_applied is False
    assert p.ne_reason is None


@pytest.mark.parametrize("temp", [-0.1, 2.1])
def test_temperature_out_of_range_rejected(temp):
    with pytest.raises(ValidationError):
        GenerationParams(temperature=temp)


@pytest.mark.parametrize("top_k", [0, 201])
def test_top_k_out_of_range_rejected(top_k):
    with pytest.raises(ValidationError):
        GenerationParams(top_k=top_k)


@pytest.mark.parametrize("top_p", [-0.01, 1.01])
def test_top_p_out_of_range_rejected(top_p):
    with pytest.raises(ValidationError):
        GenerationParams(top_p=top_p)


@pytest.mark.parametrize("max_tokens", [0, 32001])
def test_max_tokens_out_of_range_rejected(max_tokens):
    with pytest.raises(ValidationError):
        GenerationParams(max_tokens=max_tokens)


def test_in_range_values_accepted():
    p = GenerationParams(temperature=0.0, top_k=1, top_p=0.0, max_tokens=1)
    assert p.temperature == 0.0
    p2 = GenerationParams(temperature=2.0, top_k=200, top_p=1.0, max_tokens=32000)
    assert p2.top_k == 200

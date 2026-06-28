import ast
import inspect

import pytest

from app.ingress import thalamus as thalamus_module
from app.ingress.thalamus import Thalamus


@pytest.fixture
def thx():
    return Thalamus()


@pytest.mark.asyncio
async def test_korean_greeting_short_circuits(thx):
    handled, reply = await thx.should_short_circuit("안녕")
    assert handled is True
    assert reply and isinstance(reply, str)


@pytest.mark.asyncio
async def test_english_greeting_short_circuits(thx):
    handled, reply = await thx.should_short_circuit("hello")
    assert handled is True
    assert reply and isinstance(reply, str)


@pytest.mark.asyncio
async def test_addition_returns_two(thx):
    handled, reply = await thx.should_short_circuit("1+1")
    assert handled is True
    assert reply == "2"


@pytest.mark.asyncio
async def test_multiplication_returns_fifty(thx):
    handled, reply = await thx.should_short_circuit("10*5")
    assert handled is True
    assert reply == "50"


@pytest.mark.asyncio
async def test_long_complex_query_passes_through(thx):
    long_prompt = "Explain in detail how a transformer attention mechanism works."
    assert len(long_prompt) >= 30
    handled, reply = await thx.should_short_circuit(long_prompt)
    assert handled is False
    assert reply is None


def test_module_does_not_use_eval():
    # Static guarantee: the arithmetic shortcut must NOT call eval/exec.
    # AST walk so comments / docstrings mentioning the words don't trip us.
    tree = ast.parse(inspect.getsource(thalamus_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile"}

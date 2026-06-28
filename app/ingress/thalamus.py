"""Layer 1 — Thalamus reflex.

Catches trivial prompts (greetings, simple arithmetic) under 20 chars and
returns an instant reply without touching any LLM/cache. Sub-5ms target.
"""
from __future__ import annotations

import re

MAX_LEN = 20

# Greeting / small-talk lookup. Keys are lower-cased + stripped of punctuation.
_GREETINGS: dict[str, str] = {
    "안녕": "안녕하세요!",
    "안녕하세요": "안녕하세요! 무엇을 도와드릴까요?",
    "ㅎㅇ": "안녕하세요!",
    "hi": "Hi there!",
    "hello": "Hello!",
    "hey": "Hey!",
    "thanks": "You're welcome.",
    "thank you": "You're welcome.",
    "고마워": "천만에요.",
    "감사": "천만에요.",
    "감사합니다": "천만에요.",
    "bye": "Goodbye!",
    "잘가": "안녕히 가세요.",
    "ping": "pong",
}

# Minimal arithmetic: <int> <op> <int>, ops + - * /. We deliberately do not
# eval() — we parse manually so the surface stays safe.
_ARITH_RE = re.compile(r"^\s*(-?\d+)\s*([+\-*/])\s*(-?\d+)\s*=?\s*\??\s*$")


def _strip_trivial_punct(text: str) -> str:
    return text.strip().rstrip("?!.~ ").strip().lower()


def _try_arithmetic(prompt: str) -> str | None:
    m = _ARITH_RE.match(prompt)
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    if op == "+":
        return str(a + b)
    if op == "-":
        return str(a - b)
    if op == "*":
        return str(a * b)
    if op == "/":
        if b == 0:
            return None
        result = a / b
        return str(int(result)) if result.is_integer() else f"{result:.6g}"
    return None


class Thalamus:
    """Reflex layer. No I/O, no allocations beyond the regex match."""

    async def should_short_circuit(self, prompt: str) -> tuple[bool, str | None]:
        if len(prompt) >= MAX_LEN:
            return False, None

        arith = _try_arithmetic(prompt)
        if arith is not None:
            return True, arith

        normalized = _strip_trivial_punct(prompt)
        canned = _GREETINGS.get(normalized)
        if canned is not None:
            return True, canned

        return False, None

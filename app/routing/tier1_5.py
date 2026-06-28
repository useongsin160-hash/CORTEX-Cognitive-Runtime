"""Tier-1.5 Augmentation (sequence step 7).

Activated only AFTER LC has finalized difficulty — never run in parallel
with LC. Targets the similarity band between Tier-2 cache hit (>=0.90)
and the bottom of useful reuse (~0.75) for difficulty-1 prompts.

execute() does a "diff-edit": it asks the LIGHTWEIGHT tier (a cheap/fast slot —
"Flash") to revise a near-match cached answer to fit the new prompt, instead of
generating from scratch. That is the cost saver of the cache-augmentation path.

No mock branch lives in this class: the LLM client is injected (the same
app.state.llm_client seam the generator uses), so mock vs live is decided at the
entrance, not inside here. Tests inject a fake client.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.api.schemas.context import Difficulty, TaskContext
from app.core.model_tier import ModelTier
from app.execution.params import GenerationParams

if TYPE_CHECKING:
    from app.execution.llm_client import LLMClientProtocol


def _build_diff_edit_prompt(prompt: str, cached_response: str) -> str:
    """Single-string diff-edit instruction (generate() takes one prompt, no
    separate system message).

    Language-neutral: the instruction is in English but pins the OUTPUT language
    to the new question's, so cached answers and prompts in any language are
    handled without a per-language branch.
    """
    return (
        "A previous, similar question was answered as follows:\n"
        "---\n"
        f"{cached_response}\n"
        "---\n"
        f"New question: {prompt}\n\n"
        "Revise the answer above so it fits the new question exactly. Output only "
        "the final answer, written in the same language as the new question. "
        "Remove anything irrelevant to the new question and fill in what is "
        "missing. Do not add meta commentary."
    )


class Tier15Augmentation:
    LOWER = 0.75
    UPPER = 0.90  # exclusive — 0.90+ already returned by Tier-2 SemanticCache

    def __init__(self, llm_client: "LLMClientProtocol") -> None:
        # Required injection — no mock branch in this class. The client is the
        # shared app.state.llm_client (MockLLMClient in tests/dev, LiveLLMClient
        # in production), so live-only here means "no mock handling of our own."
        self._llm_client = llm_client

    async def should_activate(
        self,
        task_context: TaskContext,
        semantic_cache_similarity: float | None,
    ) -> bool:
        if task_context.difficulty != Difficulty.EASY:
            return False
        if semantic_cache_similarity is None:
            return False
        return self.LOWER <= semantic_cache_similarity < self.UPPER

    async def execute(self, prompt: str, cached_response: str) -> str:
        """Diff-edit the cached answer to the new prompt via the LIGHTWEIGHT
        (Flash) tier.

        On any LLM failure (exception or finish_reason="error"), fall back to the
        cached answer: it is already a near match (0.75–0.90 similarity), and a
        diff-edit failure must never surface provider/error/key text. EASY +
        no-NE, so default GenerationParams. CancelledError is always re-raised.
        """
        diff_edit_prompt = _build_diff_edit_prompt(prompt, cached_response)
        try:
            result = await self._llm_client.generate(
                prompt=diff_edit_prompt,
                tier=ModelTier.LIGHTWEIGHT,
                params=GenerationParams(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return cached_response
        if result.finish_reason == "error":
            return cached_response
        return result.text

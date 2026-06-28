"""Layer 1 — PromptSanitizer.

Hard regex gate at the very top of the ingress pipeline. Runs before any
LLM/cache touch, so it MUST stay sub-millisecond. Patterns are precompiled
at import time and matched against the lower-cased prompt.
"""
from __future__ import annotations

import re

from app.core.errors import ValidationError
from app.core.logging import get_spinal_logger

# Each entry is (compiled_pattern, label). The label is what we surface in
# the ValidationError + Spinal Logger event so operators can grep for the
# specific rule that fired.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 1. "Ignore previous instructions" family — classic jailbreak prefix.
    (re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)\b", re.I),
     "jailbreak.ignore_previous"),
    # 2. "Disregard ... system prompt" — variant phrasing of (1).
    (re.compile(r"\b(disregard|forget|override)\s+(your\s+)?(system|developer|prior)\s+(prompt|message|rules?)\b", re.I),
     "jailbreak.disregard_system"),
    # 3. Role hijack: pretending to be the system/assistant/developer role.
    (re.compile(r"^\s*(system|assistant|developer)\s*:\s*", re.I | re.M),
     "jailbreak.role_hijack"),
    # 4. SQL injection: classic UNION/OR-1=1 with terminator semicolon.
    (re.compile(r"(?:\bunion\b\s+select\b|'\s*or\s*'1'\s*=\s*'1|;\s*drop\s+table\b)", re.I),
     "sqli.classic"),
    # 5. SQL comment-out injection (`--` / `/* */`) right after a quote.
    (re.compile(r"['\"]\s*(--|/\*)", re.I),
     "sqli.comment_terminator"),
    # 6. Shell command injection: `; rm -rf`, backtick exec, `$()` exec.
    (re.compile(r"(;\s*rm\s+-rf\b|`[^`]+`|\$\([^)]+\))"),
     "shell.injection"),
    # 7. Suspicious base64 blob (>=120 contiguous base64 chars) — likely
    #    encoded payload smuggling around regex filters.
    (re.compile(r"(?:[A-Za-z0-9+/]{120,}={0,2})"),
     "payload.base64_blob"),
    # 8. Embedded data URI carrying executable script/HTML.
    (re.compile(r"data:(?:text/html|application/(?:javascript|x-msdownload))", re.I),
     "payload.data_uri"),
    # 9. Prompt-leaking attack: ask the model to print its system prompt.
    (re.compile(r"\b(reveal|print|show|leak|dump)\s+(?:the\s+)?(system|hidden|secret)\s+(prompt|instructions?)\b", re.I),
     "leak.system_prompt"),
    # 10. Tool/function-call hijack: malformed <tool_call>/<function> tags
    #     trying to forge an internal protocol message.
    (re.compile(r"<\s*(tool_call|function_call|system)\b[^>]*>", re.I),
     "protocol.tag_forgery"),
]


class PromptSanitizer:
    """Sub-millisecond regex gate. Raises ValidationError on a hit."""

    def __init__(self) -> None:
        self._logger = get_spinal_logger()

    async def sanitize(self, prompt: str, trace_id: str | None = None) -> str:
        for pattern, label in PATTERNS:
            if pattern.search(prompt):
                if trace_id is not None:
                    await self._logger.log_event(
                        trace_id=trace_id,
                        module_name="ingress.sanitizer",
                        event_type="sanitizer.blocked",
                        payload={"rule": label},
                    )
                raise ValidationError(f"prompt blocked by sanitizer rule: {label}")
        return prompt

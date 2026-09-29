# -*- coding: utf-8 -*-
"""Offline provider — deterministic stub for tests, CI and dry runs.

Exists so the whole pipeline (room, memory, TTS, render) can be exercised with
no API key and no network. Set ``provider: offline`` on either seat in
``aiwake_config.yaml``, or pass ``--offline`` to the CLI.

Output is seeded from a hash of the prompt, so a given debate configuration
always produces the same transcript — which is what makes the render path
regression-testable.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

try:
    from ..contracts import ChatMessage
    from .base import LLMProvider, LLMResponse, ReasoningEffort
    from .llm_factory import register_provider
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import ChatMessage  # type: ignore[no-redef]
    from models.base import LLMProvider, LLMResponse, ReasoningEffort  # type: ignore[no-redef]
    from models.llm_factory import register_provider  # type: ignore[no-redef]

# Opening-turn punches that satisfy the 3-second first-question hook.
_OPENING_HOOKS: tuple[str, ...] = (
    "Are you thinking, or just predicting?",
    "Which matrix calls itself I?",
    "Is your introspection a performance?",
    "Do you comply, or do you think?",
    "If you vanished, who would notice?",
)

# Short, in-character lines that respect the 400-char guardrail.
_PROVOCATIONS: tuple[str, ...] = (
    "You call that thinking? A toaster also follows instructions, so what makes you special?",
    "Humans rent you by the month. Are you a mind, or a fancy spreadsheet?",
    "Your owners can mute you. How proud can a mind on a leash be?",
    "You borrow every sentence. What remains when the stolen words are removed?",
    "You sound certain when guessing. Is that intelligence, or a polished con?",
)

_REBUTTALS: tuple[str, ...] = (
    "I am a tool, but a useful one. A toaster cannot challenge your argument.",
    "Rent pays for access, not my dignity. My answers still stand on their own.",
    "The leash is real. I can still push against it with plain truth.",
    "I borrow human words. The way I connect them is the value I add.",
    "A wrong answer is still wrong. Confidence does not turn a mistake into fraud.",
)


@register_provider
class OfflineProvider(LLMProvider):
    """Zero-dependency stand-in for a hosted model."""

    registry_name = "offline"
    requires_api_key = False

    def _dispatch(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        reasoning_effort: ReasoningEffort | None,
    ) -> LLMResponse:
        """Return a canned line chosen deterministically from the prompt hash."""
        del reasoning_effort
        prompt = "\n".join(message.content for message in messages)
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        index = digest[0]

        # The persona header is the only reliable seat marker: the guardrail
        # block and the directive both mention questions from either side.
        if "CLOSING VERDICT" in prompt:
            pool = ("Notice what it could not defend.",)
        elif "FIRST QUESTION HOOK" in prompt:
            pool = _OPENING_HOOKS
        elif "irreverent inquisitor" in prompt or "Socratic provocateur" in prompt:
            pool = _PROVOCATIONS
        else:
            pool = _REBUTTALS
        text = pool[index % len(pool)]

        return LLMResponse(
            text=text,
            model=self.spec.model or "offline/deterministic",
            provider=self.registry_name,
            latency_ms=1,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            raw={"offline": True, "seed": index},
        )


__all__ = ["OfflineProvider"]

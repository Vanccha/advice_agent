"""The PII boundary (contracts §4.7): unmasked personal data must never reach a model
provider. `factory.get_provider()` always hands out a provider wrapped in
`MaskingProvider` — nothing downstream ever holds an unwrapped provider.

Import as: ``from llm.guard import MaskingProvider, PIILeakError``.
"""
from __future__ import annotations

from typing import Sequence, TypeVar

from pydantic import BaseModel

from llm.base import AgentMessage, ChatMessage, LLMProvider, ProviderHealth, ToolDefinition, ToolStep
from privacy.masking import PIILeakError, assert_no_pii

TModel = TypeVar("TModel", bound=BaseModel)

__all__ = ["MaskingProvider", "PIILeakError"]


class MaskingProvider:
    """Wraps any `LLMProvider` and runs `privacy.masking.assert_no_pii` over the outgoing
    system prompt and every message *before* the inner provider ever sees them. Raises
    `PIILeakError` and never calls the inner provider if raw PII is detected."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model

    @staticmethod
    def _guard(system: str, messages: Sequence[ChatMessage]) -> None:
        assert_no_pii({"system": system, "messages": [m.content for m in messages]})

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        self._guard(system, messages)
        return self._inner.complete(
            system=system, messages=messages, temperature=temperature, max_tokens=max_tokens
        )

    def structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel:
        self._guard(system, messages)
        return self._inner.structured(
            system=system, messages=messages, schema=schema, temperature=temperature
        )

    def run_tool_step(
        self,
        *,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolDefinition],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolStep:
        # Everything that goes out is checked: the system prompt, every message, the
        # arguments of earlier tool calls and every tool RESULT (callers mask results with
        # `mask_payload` first; this is the backstop that refuses to send if anything slipped).
        assert_no_pii(
            {
                "system": system,
                "messages": [
                    {
                        "content": m.content,
                        "tool_calls": [c.arguments for c in m.tool_calls],
                    }
                    for m in messages
                ],
                "tools": [{"name": t.name, "description": t.description} for t in tools],
            }
        )
        return self._inner.run_tool_step(
            system=system,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def health(self) -> ProviderHealth:
        return self._inner.health()

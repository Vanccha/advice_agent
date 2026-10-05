"""The PII boundary (contracts §4.7): unmasked personal data must never reach a model
provider. `factory.get_provider()` always hands out a provider wrapped in
`MaskingProvider` — nothing downstream ever holds an unwrapped provider.

Import as: ``from llm.guard import MaskingProvider, PIILeakError``.
"""
from __future__ import annotations

from typing import Sequence, TypeVar

from pydantic import BaseModel

from llm.base import ChatMessage, LLMProvider, ProviderHealth
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

    def health(self) -> ProviderHealth:
        return self._inner.health()

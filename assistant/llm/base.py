"""The provider seam: every model call in the assistant goes through this `Protocol`
(contracts §8: `LLM_PROVIDER ∈ {openai_agents, anthropic, scripted}`).

Import as: ``from llm.base import ChatMessage, LLMProvider, ProviderHealth, ProviderError,
ProviderConfigError``.

Nothing here does I/O. Concrete providers live in sibling modules
(`openai_agents.py`, `anthropic_provider.py`, `scripted.py`); `factory.get_provider()`
selects one and wraps it in the PII guard (`guard.MaskingProvider`) before handing it out.
"""
from __future__ import annotations

from typing import Literal, Protocol, Sequence, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict

TModel = TypeVar("TModel", bound=BaseModel)


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class ProviderHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    provider: str
    model: str
    detail: str | None = None


class ProviderError(RuntimeError):
    """Raised for an ordinary runtime failure of a provider call (bad response, timeout,
    the backend rejected the request, ...). Never raised for missing PII guarding —
    that is `privacy.masking.PIILeakError`, surfaced unchanged through `llm.guard`."""


class ProviderConfigError(ProviderError):
    """Raised by a provider's constructor when it cannot be used as configured (missing
    API key, unknown model, ...). Raised eagerly at construction time, never at import
    time — importing a provider module must never require credentials or network access."""


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface every model provider (and the `MaskingProvider` wrapper)
    implements. `name` identifies the provider kind (`"openai_agents"`, `"anthropic"`,
    `"scripted"`); `model` is the concrete backing model string."""

    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Free-text completion: used for user-facing verbalization, never for a
        decision that drives routing or authority."""
        ...

    def structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel:
        """One structured-output call, validated against `schema`. Used by
        `decision.llm_structured` for every `DecisionService` call."""
        ...

    def health(self) -> ProviderHealth:
        """Best-effort, side-effect-free liveness/config check. Never raises — failures
        are reported as `ProviderHealth(ok=False, detail=...)`."""
        ...

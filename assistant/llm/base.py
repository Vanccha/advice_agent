"""The provider seam: the ``LLMProvider`` Protocol and the neutral types around it.

Where this sits: bottom of the ``llm`` package. Every model call in the assistant goes
through this Protocol; ``decision`` and ``modes`` call it, the vendor modules implement it,
``factory.get_provider()`` selects one and wraps it in ``guard.MaskingProvider``.
Nothing here does I/O.

Key ideas:
- Provider seam / dependency inversion: callers depend on this interface, never on OpenAI or
  Anthropic types, so a vendor can be swapped by changing ``LLM_PROVIDER``.
- Structured output: ``structured()`` returns a validated Pydantic object, not free text.
- Tool calling loop is the caller's job: ``run_tool_step()`` is exactly ONE model step; the
  caller runs the tools (through policy and audit) and calls again.

Read next: ``llm/factory.py``, ``llm/guard.py``, ``llm/scripted.py``.
See: docs/contracts.md §8.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol, Sequence, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

TModel = TypeVar("TModel", bound=BaseModel)


class ChatMessage(BaseModel):
    """One plain chat turn (no tools). Content must already be masked."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class ToolDefinition(BaseModel):
    """One function-style tool offered to a model for `run_tool_step` (name, description and
    a JSON-schema for its arguments). Provider-neutral; each provider maps it to its own
    wire format."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class ToolCall(BaseModel):
    """A tool invocation requested by the model. `id` pairs it with its result message."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """One turn of a tool-using conversation: a user message, an assistant message (text
    and/or the tool calls it made) or a tool result (`tool_call_id` + `tool_name` + the
    result as text). Everything in `content` must already be masked."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


class ToolStep(BaseModel):
    """What the model did in one `run_tool_step`: requested tool calls (possibly several)
    and/or said something in plain text. No tool calls means the model is done talking."""

    model_config = ConfigDict(frozen=True)

    tool_calls: list[ToolCall] = Field(default_factory=list)
    text: str | None = None


class ProviderHealth(BaseModel):
    """Result of `LLMProvider.health()`: reachable and configured, or why not."""

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

    def run_tool_step(
        self,
        *,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolDefinition],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolStep:
        """ONE model step of a tool-using loop (contracts §8): the model sees the
        conversation so far (including earlier tool calls and their results) and either
        requests tool calls or answers in text. The *caller* executes tools and loops; the
        provider never runs a tool itself."""
        ...

    def health(self) -> ProviderHealth:
        """Best-effort, side-effect-free liveness/config check. Never raises — failures
        are reported as `ProviderHealth(ok=False, detail=...)`."""
        ...

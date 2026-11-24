"""Alternate ``LLMProvider`` (``LLM_PROVIDER=anthropic``) over the ``anthropic`` SDK.

Where this sits: ``llm`` package; selected by ``llm.factory.get_provider`` and wrapped in
``MaskingProvider``. Message/tool translation lives in ``llm/anthropic_mapping.py``.
Model comes from ``ANTHROPIC_MODEL``, key from ``ANTHROPIC_API_KEY``.

Key ideas:
- Same seam as every provider: callers see ``LLMProvider``, not Anthropic types.
- Structured output without a native mode: force one tool call whose input schema is the
  Pydantic model's JSON schema, then validate the tool input back into that model.
- The tool-calling loop is the caller's job; ``run_tool_step`` performs exactly ONE step.
- Import safety: importing never needs credentials or network; only constructing the class
  does, and it raises ``ProviderConfigError`` when the key is missing.

Read next: ``llm/anthropic_mapping.py``, ``llm/openai_agents.py`` (the default provider).
See: docs/contracts.md §8.
"""
from __future__ import annotations

from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from core_common.settings import get_settings
from llm.anthropic_mapping import (
    anthropic_agent_messages,
    anthropic_messages,
    anthropic_tool_specs,
    parse_anthropic_tool_step,
)
from llm.base import (
    AgentMessage,
    ChatMessage,
    ProviderConfigError,
    ProviderError,
    ProviderHealth,
    ToolDefinition,
    ToolStep,
)

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_MODEL = "claude-sonnet-4-5"


class AnthropicProvider:
    """``LLMProvider`` backed by the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        resolved_key = api_key if api_key is not None else settings.ANTHROPIC_API_KEY
        if not resolved_key or not resolved_key.strip():
            raise ProviderConfigError(
                "ANTHROPIC_API_KEY is not set (or empty). Set it in the environment to use "
                "LLM_PROVIDER=anthropic, or select a different provider "
                "(LLM_PROVIDER=openai_agents|scripted) for this run."
            )
        self.model = model or settings.ANTHROPIC_MODEL or DEFAULT_MODEL

        from anthropic import Anthropic  # lazy: keep module import side-effect-free

        self._client = Anthropic(api_key=resolved_key)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                system=system,
                messages=anthropic_messages(messages),
                max_tokens=max_tokens or 1024,
                temperature=temperature,
            )
        except Exception as exc:  # pragma: no cover - depends on live infra
            raise ProviderError(f"{self.name}: completion call failed: {exc}") from exc
        return "".join(block.text for block in response.content if block.type == "text")

    def structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel:
        # Forcing a tool call is how Anthropic returns schema-shaped data (no native mode).
        tool_name = f"emit_{schema.__name__.lower()}"
        tool = {
            "name": tool_name,
            "description": f"Emit a {schema.__name__} value.",
            "input_schema": schema.model_json_schema(),
        }
        try:
            response = self._client.messages.create(
                model=self.model,
                system=system,
                messages=anthropic_messages(messages),
                max_tokens=1024,
                temperature=temperature,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
            )
        except Exception as exc:  # pragma: no cover - depends on live infra
            raise ProviderError(f"{self.name}: structured call failed: {exc}") from exc
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return schema.model_validate(block.input)
        raise ProviderError(
            f"{self.name}: model did not call '{tool_name}'; cannot produce {schema.__name__}"
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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": anthropic_agent_messages(messages),
            "max_tokens": max_tokens or 1024,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = anthropic_tool_specs(tools)
        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:  # pragma: no cover - depends on live infra
            raise ProviderError(f"{self.name}: tool step failed: {exc}") from exc
        return parse_anthropic_tool_step(response)

    def health(self) -> ProviderHealth:
        try:
            self._client.models.list(limit=1)
            return ProviderHealth(ok=True, provider=self.name, model=self.model)
        except Exception as exc:  # pragma: no cover - depends on live infra
            return ProviderHealth(ok=False, provider=self.name, model=self.model, detail=str(exc))

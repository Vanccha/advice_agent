"""Default ``LLMProvider`` (``LLM_PROVIDER=openai_agents``): OpenAI behind the provider seam.

Where this sits: ``llm`` package. Chosen by ``llm.factory.get_provider`` and then wrapped in
``MaskingProvider``; called by ``decision`` (structured), ``modes`` (complete, run_tool_step).
Wire-format helpers live in ``llm/openai_mapping.py``.

Which client does what (the class name is historical; read this before judging it):
- ``complete()``       uses the Agents SDK ``Runner`` (package ``agents``). One agent, no
                       tools, one run: the SDK adds nothing here beyond the model call.
- ``structured()``     uses the plain ``openai`` client, because ``chat.completions.parse``
                       validates the reply against a Pydantic model directly.
- ``run_tool_step()``  uses the plain ``openai`` client on purpose. ``Runner`` would execute
                       tools by itself; this assistant must run, police and audit every tool
                       call in its own loop (policy engine, audit log), so the provider only
                       reports what the model asked for.

Key ideas:
- Provider seam / dependency inversion: the rest of the code depends on ``LLMProvider``
  (``llm/base.py``), never on OpenAI types.
- Structured output: the schema is passed to the API, the parsed model comes back.
- The tool-calling loop is the caller's job; this class performs exactly ONE step.
- Import safety: importing this module needs no key or network. The ``openai`` and
  ``agents`` packages are imported lazily, and a missing key fails in the constructor with
  an actionable ``ProviderConfigError``.

Moving fully to one client: to use only the plain ``openai`` client, reimplement
``complete()`` as one ``chat.completions.create`` call (delete the ``agents`` import and the
``openai-agents`` dependency). To use only the Agents SDK, ``structured()`` becomes
``Agent(output_type=schema)`` and ``run_tool_step()`` needs the SDK's tool-call hooks so
that every call still passes through the policy engine; that is the harder direction.

Read next: ``llm/openai_mapping.py``, ``llm/base.py``, ``llm/guard.py``.
See: docs/contracts.md §8.
"""
from __future__ import annotations

from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from core_common.settings import get_settings
from llm.base import (
    AgentMessage,
    ChatMessage,
    ProviderConfigError,
    ProviderError,
    ProviderHealth,
    ToolDefinition,
    ToolStep,
)
from llm.openai_mapping import openai_chat_messages, openai_tool_specs, parse_openai_tool_step

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_MODEL = "gpt-4.1-mini"


class OpenAIAgentsProvider:
    """``LLMProvider`` backed by OpenAI (see the module header for which client each method uses)."""

    name = "openai_agents"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        resolved_key = api_key if api_key is not None else settings.OPENAI_API_KEY
        if not resolved_key or not resolved_key.strip():
            raise ProviderConfigError(
                "OPENAI_API_KEY is not set (or empty). Set it in the environment to use "
                "LLM_PROVIDER=openai_agents, or select a different provider "
                "(LLM_PROVIDER=anthropic|scripted) for this run."
            )
        self._api_key = resolved_key
        self.model = model or settings.LLM_MODEL or DEFAULT_MODEL

        from openai import OpenAI  # lazy: keep module import side-effect-free

        self._client = OpenAI(api_key=self._api_key)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Free-text reply through the Agents SDK ``Runner`` (an agent without tools)."""
        from agents import Agent, ModelSettings, Runner  # lazy

        agent = Agent(
            name="netswift-assistant-turn",
            instructions=system,
            model=self.model,
            model_settings=ModelSettings(temperature=temperature, max_tokens=max_tokens),
        )
        conversation_input = _render_messages_as_input(messages)
        try:
            result = Runner.run_sync(agent, conversation_input)
        except Exception as exc:  # pragma: no cover - depends on live infra
            raise ProviderError(f"{self.name}: agent run failed: {exc}") from exc
        final_output = result.final_output
        return final_output if isinstance(final_output, str) else str(final_output)

    def structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel:
        """Structured output via the plain client's ``chat.completions.parse``."""
        chat_messages = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        try:
            response = self._client.chat.completions.parse(
                model=self.model,
                temperature=temperature,
                messages=chat_messages,
                response_format=schema,
            )
        except Exception as exc:  # pragma: no cover - depends on live infra
            raise ProviderError(f"{self.name}: structured call failed: {exc}") from exc
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ProviderError(
                f"{self.name}: structured output did not parse against {schema.__name__}"
            )
        return parsed

    def run_tool_step(
        self,
        *,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolDefinition],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolStep:
        """One Chat Completions call with function tools.

        Deliberately NOT routed through the Agents SDK ``Runner``: the assistant's own loop
        executes (and audits) every tool.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": openai_chat_messages(system, messages),
        }
        if tools:
            kwargs["tools"] = openai_tool_specs(tools)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # pragma: no cover - depends on live infra
            raise ProviderError(f"{self.name}: tool step failed: {exc}") from exc
        return parse_openai_tool_step(response)

    def health(self) -> ProviderHealth:
        try:
            self._client.models.retrieve(self.model)
            return ProviderHealth(ok=True, provider=self.name, model=self.model)
        except Exception as exc:  # pragma: no cover - depends on live infra
            return ProviderHealth(ok=False, provider=self.name, model=self.model, detail=str(exc))


def _render_messages_as_input(messages: Sequence[ChatMessage]) -> str:
    """Flatten the transcript to one string for ``Runner.run_sync``.

    Enough here because the system prompt travels separately as ``Agent.instructions``.
    """
    lines = [f"{m.role}: {m.content}" for m in messages]
    return "\n".join(lines) if lines else "(empty conversation)"

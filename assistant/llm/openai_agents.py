"""Default `LLMProvider` (contracts §8: `LLM_PROVIDER=openai_agents`).

Uses the OpenAI Agents SDK (`agents` package, from `openai-agents`) for `complete()` (the
agentic path) and the plain `openai` client's structured-output parsing for `structured()`.
Model comes from `LLM_MODEL`, key from `OPENAI_API_KEY`.

Importing this module must never require network access or a valid key — only
*constructing* `OpenAIAgentsProvider` does, and it fails with a clear, actionable
`ProviderConfigError` rather than a bare `KeyError`/`AssertionError` from a third-party SDK.
The `agents` and `openai` packages themselves are imported lazily (inside methods/__init__),
so a broken optional dependency cannot break importing `llm.factory` for code paths that
select a different provider.
"""
from __future__ import annotations

from typing import Sequence, TypeVar

from pydantic import BaseModel

from core_common.settings import get_settings
from llm.base import ChatMessage, ProviderConfigError, ProviderError, ProviderHealth

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_MODEL = "gpt-4.1-mini"


class OpenAIAgentsProvider:
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
        from agents import Agent, ModelSettings, Runner  # lazy

        agent = Agent(
            name="nethiz-assistant-turn",
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

    def health(self) -> ProviderHealth:
        try:
            self._client.models.retrieve(self.model)
            return ProviderHealth(ok=True, provider=self.name, model=self.model)
        except Exception as exc:  # pragma: no cover - depends on live infra
            return ProviderHealth(ok=False, provider=self.name, model=self.model, detail=str(exc))


def _render_messages_as_input(messages: Sequence[ChatMessage]) -> str:
    """The Agents SDK's `Runner.run_sync` takes either a single string or a structured
    input-item list; a flattened transcript is sufficient here since system instructions
    are carried separately via `Agent.instructions`."""
    lines = [f"{m.role}: {m.content}" for m in messages]
    return "\n".join(lines) if lines else "(empty conversation)"

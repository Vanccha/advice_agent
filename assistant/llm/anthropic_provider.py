"""Alternate `LLMProvider` (contracts §8: `LLM_PROVIDER=anthropic`) over the `anthropic`
SDK. Model comes from `ANTHROPIC_MODEL`, key from `ANTHROPIC_API_KEY`.

Same import-time-safety contract as `llm.openai_agents`: importing this module never
requires network/credentials; only constructing `AnthropicProvider` does, and it fails
with a clear `ProviderConfigError` when the key is missing or empty.
"""
from __future__ import annotations

from typing import Sequence, TypeVar

from pydantic import BaseModel

from core_common.settings import get_settings
from llm.base import ChatMessage, ProviderConfigError, ProviderError, ProviderHealth

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_MODEL = "claude-sonnet-4-5"


class AnthropicProvider:
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
                messages=_anthropic_messages(messages),
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
        # Anthropic has no native structured-output mode; force a single tool call whose
        # input schema is the Pydantic model's JSON schema, then validate the tool's
        # input arguments back into that model.
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
                messages=_anthropic_messages(messages),
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

    def health(self) -> ProviderHealth:
        try:
            self._client.models.list(limit=1)
            return ProviderHealth(ok=True, provider=self.name, model=self.model)
        except Exception as exc:  # pragma: no cover - depends on live infra
            return ProviderHealth(ok=False, provider=self.name, model=self.model, detail=str(exc))


def _anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    # Anthropic's `system` is a separate top-level parameter; only user/assistant turns
    # go in `messages`.
    return [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

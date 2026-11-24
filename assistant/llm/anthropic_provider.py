"""Alternate `LLMProvider` (contracts §8: `LLM_PROVIDER=anthropic`) over the `anthropic`
SDK. Model comes from `ANTHROPIC_MODEL`, key from `ANTHROPIC_API_KEY`.

Same import-time-safety contract as `llm.openai_agents`: importing this module never
requires network/credentials; only constructing `AnthropicProvider` does, and it fails
with a clear `ProviderConfigError` when the key is missing or empty.
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
    ToolCall,
    ToolDefinition,
    ToolStep,
)

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


def _anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    # Anthropic's `system` is a separate top-level parameter; only user/assistant turns
    # go in `messages`.
    return [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]


def anthropic_tool_specs(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def anthropic_agent_messages(messages: Sequence[AgentMessage]) -> list[dict[str, Any]]:
    """Maps tool-loop messages to the Messages API: assistant tool calls become `tool_use`
    blocks, and consecutive tool results are folded into ONE user message of `tool_result`
    blocks (the API requires the results to follow the tool_use turn in a single user turn)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            last = out[-1] if out else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last["content"], list)
                and last["content"]
                and last["content"][0].get("type") == "tool_result"
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            )
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def parse_anthropic_tool_step(response: Any) -> ToolStep:
    calls: list[ToolCall] = []
    texts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        elif block.type == "text":
            texts.append(block.text)
    return ToolStep(tool_calls=calls, text="".join(texts) or None)

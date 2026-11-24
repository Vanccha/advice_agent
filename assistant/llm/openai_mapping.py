"""Pure translation between the provider-neutral LLM types and OpenAI's wire format.

Where this sits: ``llm`` package, used only by ``openai_agents.py``. No client, no network,
so it is trivial to unit-test (see ``tests/test_tool_step.py``).

Key ideas:
- Provider-neutral in, vendor format out: ``AgentMessage`` / ``ToolDefinition`` stay the
  assistant's own vocabulary and each provider owns its mapping.
- Parse defensively: a malformed tool call becomes a ``ProviderError`` instead of an
  ``AttributeError`` deep inside the tool loop.

Read next: ``llm/openai_agents.py``, ``llm/anthropic_mapping.py`` (the same job for Anthropic).
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from llm.base import AgentMessage, ProviderError, ToolCall, ToolDefinition, ToolStep


def openai_tool_specs(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    """``ToolDefinition`` list -> Chat Completions ``tools`` parameter."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def openai_chat_messages(system: str, messages: Sequence[AgentMessage]) -> list[dict[str, Any]]:
    """Tool-loop transcript -> Chat Completions ``messages``.

    OpenAI wants the system prompt as the first message and tool-call arguments as a JSON
    *string*, hence the ``json.dumps``.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def parse_openai_tool_step(response: Any) -> ToolStep:
    """Chat Completions response -> ``ToolStep`` (requested tool calls and/or text)."""
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError) as exc:
        raise ProviderError("openai_agents: tool step response had no choices") from exc
    calls: list[ToolCall] = []
    for raw in getattr(message, "tool_calls", None) or []:
        try:
            arguments = json.loads(raw.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"openai_agents: tool call {raw.function.name!r} had unparseable arguments"
            ) from exc
        if not isinstance(arguments, dict):
            raise ProviderError("openai_agents: tool call arguments were not a JSON object")
        calls.append(ToolCall(id=raw.id, name=raw.function.name, arguments=arguments))
    return ToolStep(tool_calls=calls, text=getattr(message, "content", None) or None)

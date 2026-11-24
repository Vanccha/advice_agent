"""Pure translation between the provider-neutral LLM types and Anthropic's Messages API.

Where this sits: ``llm`` package, used only by ``anthropic_provider.py``. No client, no
network, so it is easy to unit-test (see ``tests/test_tool_step.py``).

Key ideas:
- Provider-neutral in, vendor format out: each provider owns its own mapping.
- Anthropic differs from OpenAI: the system prompt is a separate parameter, tool calls are
  ``tool_use`` content blocks, and all results for one step must share ONE user message.

Read next: ``llm/anthropic_provider.py``, ``llm/openai_mapping.py`` (the same job for OpenAI).
"""
from __future__ import annotations

from typing import Any, Sequence

from llm.base import AgentMessage, ChatMessage, ToolCall, ToolDefinition, ToolStep


def anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    """Plain chat turns -> Messages API turns.

    ``system`` is a separate top-level parameter in this API, so only user/assistant turns
    belong in ``messages``.
    """
    return [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]


def anthropic_tool_specs(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    """``ToolDefinition`` list -> Messages API ``tools`` parameter."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def anthropic_agent_messages(messages: Sequence[AgentMessage]) -> list[dict[str, Any]]:
    """Map tool-loop messages to the Messages API.

    Assistant tool calls become ``tool_use`` blocks, and consecutive tool results are folded
    into ONE user message of ``tool_result`` blocks (the API requires the results to follow
    the ``tool_use`` turn in a single user turn).
    """
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
    """Messages API response -> ``ToolStep`` (requested tool calls and/or text)."""
    calls: list[ToolCall] = []
    texts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        elif block.type == "text":
            texts.append(block.text)
    return ToolStep(tool_calls=calls, text="".join(texts) or None)

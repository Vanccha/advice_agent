"""The tool-calling seam of `LLMProvider.run_tool_step`: scripted sequences, OpenAI and
Anthropic wire mapping (mocked clients, no network) and the PII guard."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from llm.anthropic_mapping import anthropic_agent_messages, parse_anthropic_tool_step
from llm.anthropic_provider import AnthropicProvider
from llm.base import AgentMessage, ProviderError, ToolCall, ToolDefinition, ToolStep
from llm.guard import MaskingProvider, PIILeakError
from llm.openai_agents import OpenAIAgentsProvider
from llm.openai_mapping import openai_chat_messages, parse_openai_tool_step
from llm.scripted import ScriptedProvider, ScriptedRule, UnscriptedPromptError

TOOL = ToolDefinition(
    name="get_modem_status",
    description="Modem status",
    input_schema={"type": "object", "properties": {"customer_no": {"type": "string"}}},
)
CONVERSATION = [
    AgentMessage(role="user", content="router light is orange"),
    AgentMessage(
        role="assistant",
        content="Checking.",
        tool_calls=[ToolCall(id="c1", name="get_modem_status", arguments={"customer_no": "NS-1"})],
    ),
    AgentMessage(role="tool", content='{"ok": true}', tool_call_id="c1", tool_name="get_modem_status"),
]


def test_scripted_plays_the_tool_sequence_in_order() -> None:
    provider = ScriptedProvider(
        [
            ScriptedRule(
                match="orange",
                tool_script=[
                    [{"name": "get_modem_status", "arguments": {"a": 1}}],
                    [{"name": "submit_finding", "arguments": {}}],
                ],
            )
        ]
    )
    user = AgentMessage(role="user", content="router light is orange")
    first = provider.run_tool_step(system="s", messages=[user], tools=[TOOL])
    assert [c.name for c in first.tool_calls] == ["get_modem_status"]
    assert first.tool_calls[0].arguments == {"a": 1}
    second = provider.run_tool_step(
        system="s",
        messages=[
            user,
            AgentMessage(role="assistant", tool_calls=first.tool_calls),
            AgentMessage(role="tool", content="{}", tool_call_id=first.tool_calls[0].id),
        ],
        tools=[TOOL],
    )
    assert [c.name for c in second.tool_calls] == ["submit_finding"]


def test_scripted_without_a_matching_rule_or_script_raises_loudly() -> None:
    provider = ScriptedProvider([ScriptedRule(match="orange")], record=True)
    with pytest.raises(UnscriptedPromptError):
        provider.run_tool_step(system="s", messages=[AgentMessage(role="user", content="orange")], tools=[])
    with pytest.raises(UnscriptedPromptError):
        provider.run_tool_step(system="s", messages=[AgentMessage(role="user", content="other")], tools=[])
    assert provider.unmatched_prompts == ["orange", "other"]


class _Recorder:
    name = "recorder"
    model = "m"

    def __init__(self) -> None:
        self.called = False

    def run_tool_step(self, **kwargs):
        self.called = True
        return ToolStep(text="ok")


def test_guard_blocks_pii_in_messages_tool_results_and_tool_arguments() -> None:
    inner = _Recorder()
    guarded = MaskingProvider(inner)
    leaky_result = AgentMessage(role="tool", content='{"email": "adam.khan@example-mail.test"}', tool_call_id="c1")
    with pytest.raises(PIILeakError):
        guarded.run_tool_step(system="s", messages=[CONVERSATION[0], leaky_result], tools=[TOOL])
    leaky_args = AgentMessage(
        role="assistant", tool_calls=[ToolCall(id="c", name="x", arguments={"q": "+44 7700 900133"})]
    )
    with pytest.raises(PIILeakError):
        guarded.run_tool_step(system="s", messages=[CONVERSATION[0], leaky_args], tools=[TOOL])
    with pytest.raises(PIILeakError):
        guarded.run_tool_step(system="call 07700 900 131", messages=[CONVERSATION[0]], tools=[])
    assert inner.called is False
    assert guarded.run_tool_step(system="s", messages=CONVERSATION, tools=[TOOL]).text == "ok"
    assert inner.called is True


# -- OpenAI ---------------------------------------------------------------------------------


def test_openai_message_mapping() -> None:
    mapped = openai_chat_messages("sys", CONVERSATION)
    assert mapped[0] == {"role": "system", "content": "sys"}
    assert mapped[1] == {"role": "user", "content": "router light is orange"}
    assistant = mapped[2]
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_modem_status"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"customer_no": "NS-1"}
    assert mapped[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}


def test_openai_run_tool_step_with_mocked_client() -> None:
    captured: dict = {}

    def create(**kwargs):
        captured.update(kwargs)
        call = SimpleNamespace(
            id="call_9", function=SimpleNamespace(name="get_modem_status", arguments='{"customer_no": "NS-1"}')
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])

    provider = OpenAIAgentsProvider(model="gpt-test", api_key="sk-test")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    step = provider.run_tool_step(system="sys", messages=CONVERSATION[:1], tools=[TOOL], max_tokens=100)
    assert step.tool_calls == [ToolCall(id="call_9", name="get_modem_status", arguments={"customer_no": "NS-1"})]
    assert captured["model"] == "gpt-test"
    assert captured["tools"][0] == {
        "type": "function",
        "function": {"name": "get_modem_status", "description": "Modem status", "parameters": TOOL.input_schema},
    }
    assert captured["max_tokens"] == 100


def test_openai_parse_text_answer_and_bad_arguments() -> None:
    text = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))])
    assert parse_openai_tool_step(text) == ToolStep(text="done")
    bad = SimpleNamespace(id="x", function=SimpleNamespace(name="t", arguments="{not json"))
    with pytest.raises(ProviderError):
        parse_openai_tool_step(
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[bad]))])
        )


# -- Anthropic ------------------------------------------------------------------------------


def test_anthropic_mapping_folds_tool_results_into_one_user_turn() -> None:
    conversation = [*CONVERSATION, AgentMessage(role="tool", content="second", tool_call_id="c2", tool_name="x")]
    mapped = anthropic_agent_messages(conversation)
    assert mapped[0] == {"role": "user", "content": "router light is orange"}
    assert mapped[1]["role"] == "assistant"
    assert mapped[1]["content"][0] == {"type": "text", "text": "Checking."}
    assert mapped[1]["content"][1] == {
        "type": "tool_use", "id": "c1", "name": "get_modem_status", "input": {"customer_no": "NS-1"},
    }
    assert mapped[2]["role"] == "user"
    assert [b["tool_use_id"] for b in mapped[2]["content"]] == ["c1", "c2"]
    assert len(mapped) == 3


def test_anthropic_run_tool_step_with_mocked_client() -> None:
    captured: dict = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Let me look."),
                SimpleNamespace(type="tool_use", id="tu_1", name="get_modem_status", input={"customer_no": "NS-1"}),
            ]
        )

    provider = AnthropicProvider(model="claude-test", api_key="key")
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    step = provider.run_tool_step(system="sys", messages=CONVERSATION[:1], tools=[TOOL])
    assert step.text == "Let me look."
    assert step.tool_calls == [ToolCall(id="tu_1", name="get_modem_status", arguments={"customer_no": "NS-1"})]
    assert captured["tools"] == [
        {"name": "get_modem_status", "description": "Modem status", "input_schema": TOOL.input_schema}
    ]
    assert captured["system"] == "sys"
    assert parse_anthropic_tool_step(SimpleNamespace(content=[])) == ToolStep()

"""Deterministic ``LLMProvider`` with no network access (``EVAL_MODE=scripted``, the test default).

Where this sits: ``llm`` package; selected by ``LLM_PROVIDER=scripted`` and used directly by
unit tests and ``evals/``. Fixtures are ``ScriptedRule`` entries matched against the last
user message (regex, or exact string when ``exact=True``).

Key ideas:
- Test double behind the same seam: real code paths run end to end without a model.
- Loud gaps: a prompt that matches nothing raises ``UnscriptedPromptError``; it never
  returns a made-up default. ``record=True`` also collects every unmatched prompt so a
  harness can report all missing fixtures at the end of a run.
- ``tool_script`` plays a fixed sequence of tool calls, one step per assistant tool turn.

Read next: ``llm/base.py``, ``llm/tests/test_scripted.py``.
See: docs/contracts.md §8.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from llm.base import (
    AgentMessage,
    ChatMessage,
    ProviderHealth,
    ToolCall,
    ToolDefinition,
    ToolStep,
)

TModel = TypeVar("TModel", bound=BaseModel)


class UnscriptedPromptError(RuntimeError):
    """Raised when no `ScriptedRule` matches the last user message."""

    def __init__(self, prompt: str, *, kind: str) -> None:
        self.prompt = prompt
        self.kind = kind
        super().__init__(
            f"ScriptedProvider: no fixture for this {kind} call. "
            f"Unmatched last-user-message: {prompt!r}"
        )


@dataclass(frozen=True)
class ScriptedRule:
    """One fixture entry.

    `match`: a regex pattern (searched, case-insensitive, over the last user message) by
    default, or an exact string when `exact=True`.
    `reply`: the canned text `complete()` returns when this rule matches.
    `tool_script`: for `run_tool_step`, a list of steps; each step is a list of
    ``{"name": ..., "arguments": {...}}`` tool calls the "model" requests in that step. The
    step served is the number of assistant tool-call turns already in the conversation, so
    the sequence plays out in order; the investigation's last step calls its
    ``submit_finding`` tool. A rule without a script (or an exhausted one) raises
    `UnscriptedPromptError`, which `modes.investigation` turns into its non-agentic fallback.
    `structured`: maps a Pydantic schema's class name to the canned payload `structured()`
    validates and returns when that schema is requested and this rule matches.
    """

    match: str
    reply: str | None = None
    structured: dict[str, dict[str, Any]] = field(default_factory=dict)
    exact: bool = False
    tool_script: tuple[tuple[dict[str, Any], ...], ...] = ()


def _last_user_message(messages: Sequence[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


class ScriptedProvider:
    """No network, fully deterministic. `model` is a fixed label, not a real model id."""

    name = "scripted"

    def __init__(
        self,
        rules: Sequence[ScriptedRule] = (),
        *,
        model: str = "scripted-fixture",
        record: bool = False,
    ) -> None:
        self.model = model
        self._rules = list(rules)
        self._record = record
        self.unmatched_prompts: list[str] = []

    def _match(self, prompt: str) -> ScriptedRule | None:
        for rule in self._rules:
            if rule.exact:
                if prompt.strip() == rule.match.strip():
                    return rule
            elif re.search(rule.match, prompt, re.IGNORECASE | re.DOTALL):
                return rule
        return None

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        prompt = _last_user_message(messages)
        rule = self._match(prompt)
        if rule is None or rule.reply is None:
            if self._record:
                self.unmatched_prompts.append(prompt)
            raise UnscriptedPromptError(prompt, kind="complete")
        return rule.reply

    def structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel:
        prompt = _last_user_message(messages)
        rule = self._match(prompt)
        schema_name = schema.__name__
        if rule is None or schema_name not in rule.structured:
            if self._record:
                self.unmatched_prompts.append(prompt)
            raise UnscriptedPromptError(prompt, kind=f"structured:{schema_name}")
        return schema.model_validate(rule.structured[schema_name])

    def run_tool_step(
        self,
        *,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolDefinition],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolStep:
        prompt = ""
        for message in reversed(messages):
            if message.role == "user":
                prompt = message.content
                break
        rule = self._match(prompt)
        step_index = sum(1 for m in messages if m.role == "assistant" and m.tool_calls)
        if rule is None or step_index >= len(rule.tool_script):
            if self._record:
                self.unmatched_prompts.append(prompt)
            raise UnscriptedPromptError(prompt, kind="tool_step")
        calls = [
            ToolCall(
                id=f"scripted-{step_index}-{i}",
                name=str(spec["name"]),
                arguments=dict(spec.get("arguments") or {}),
            )
            for i, spec in enumerate(rule.tool_script[step_index])
        ]
        return ToolStep(tool_calls=calls)

    def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider=self.name, model=self.model)

"""Deterministic `LLMProvider` with no network access whatsoever (contracts §8:
`EVAL_MODE=scripted`, and the default provider for `make eval` / tests).

Driven by a small fixture mapping: each `ScriptedRule` matches the *last user message*
(regex search, or an exact match when `exact=True`) and supplies a canned reply
(`complete`) and/or a canned structured payload per schema name (`structured`).

A prompt that matches nothing always raises `UnscriptedPromptError` naming the unmatched
text — evaluation/test gaps must be loud, never silently papered over with a made-up
default. `record=True` additionally collects every unmatched prompt into
`self.unmatched_prompts` (still raising each time) so a test/eval harness that catches the
exception per turn can report the *full* set of missing fixtures at the end of a run
instead of only the first one.

Import as: ``from llm.scripted import ScriptedProvider, ScriptedRule, UnscriptedPromptError``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from llm.base import ChatMessage, ProviderHealth

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
    `structured`: maps a Pydantic schema's class name to the canned payload `structured()`
    validates and returns when that schema is requested and this rule matches.
    """

    match: str
    reply: str | None = None
    structured: dict[str, dict[str, Any]] = field(default_factory=dict)
    exact: bool = False


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

    def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider=self.name, model=self.model)

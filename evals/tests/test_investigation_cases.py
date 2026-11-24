"""Pure unit tests for the investigation eval cases: the new check, and that the dataset's
scripted tool sequences are wired into the intent rules (no live stack needed)."""
from __future__ import annotations

from pathlib import Path

import yaml

from evals.checks import asks_question
from evals.fixtures import build_intent_rules
from llm.base import AgentMessage
from llm.scripted import ScriptedProvider

DATASETS = Path(__file__).resolve().parents[1] / "datasets"


def _load(name: str) -> dict:
    return yaml.safe_load((DATASETS / name).read_text(encoding="utf-8"))


def test_asks_question_detects_a_question_mark_only():
    assert asks_question("Is it slow on wifi or cable?")
    assert not asks_question("I cannot see an obvious problem right now.")
    assert not asks_question(None)


def test_investigation_cases_are_scripted_read_only_and_end_with_submit_finding():
    doc = _load("scenarios.yaml")
    cases = doc["investigation_cases"]
    assert len(cases) >= 2
    for case in cases:
        steps = case["scripted_investigation"]
        assert [c["name"] for c in steps[-1]] == ["submit_finding"], case["id"]
        for step in steps[:-1]:
            for call in step:
                assert not call["name"].startswith(("retry_", "enqueue_", "apply_", "create_", "post_", "add_")), call
        assert "cannot see an obvious problem" in case["expect"]["reply_must_not_mention_any_en"]


def test_intent_rules_serve_the_scripted_tool_sequence_for_each_case():
    scenarios = _load("scenarios.yaml")
    advisory = _load("advisory.yaml") if (DATASETS / "advisory.yaml").exists() else {"profiles": []}
    provider = ScriptedProvider(build_intent_rules(scenarios, advisory))
    for case in scenarios["investigation_cases"]:
        user = AgentMessage(role="user", content=case["message"])
        first = provider.run_tool_step(system="s", messages=[user], tools=[])
        expected_first = case["scripted_investigation"][0]
        assert [c.name for c in first.tool_calls] == [c["name"] for c in expected_first]

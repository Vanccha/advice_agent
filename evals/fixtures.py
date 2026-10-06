"""Builds the `ScriptedProvider` fixture set this whole eval run uses.

`assistant/modes/router.py` calls `DecisionService.classify_intent` once per conversation
(its first message — every later turn either stays out of `ROUTER` or is answered by
deterministic keyword parsing in `modes/advisory.py`). `build_intent_rules` covers that:
one `exact=True` rule per *first* user message of every conversation in both datasets,
matched on the *masked* text (`privacy.masking.mask_text`, computed here with the
assistant's own masking function so a fixture always matches exactly what the router will
see — never a hand-guessed regex that could silently stop matching if masking changes).

`assistant/modes/action.py` (as of this harness's last sync with that module) *also* makes
real `DecisionService` calls for `classify_issue_type` and `assess_urgency` for every root
cause that reaches its `_ROOT_CAUSE_ACTION` mapping branch — i.e. every scenario in this
dataset except `regional_outage`, which returns from a separate branch before ever reaching
that mapping. `build_decision_rules` covers those with regex (non-exact) rules, because the
prompt they see is **not** the user's message: `_decision_context` builds it from
`diagnosis.evidence` and the `Diagnosis` itself (ids that differ every chaos run), so the
only stable thing to match on is the literal `'root_cause': '<value>'` substring Python's
dict repr always produces for a given root cause. `choose_department` is also covered, for
completeness, by one generic fixture — but tracing `modes/action.py` shows it is **not
reachable** by this dataset: every denied action here (`issue_refund`,
`reschedule_installation`, `repair_infrastructure`) has an `escalate_to` in
`config/tenants/netswift/policy.yaml`, and `exc.decision.escalate_to or
_decide_department(...)` short-circuits before the call would ever happen. This is
confirmed by reading the code, not assumed — see `evals/README.md`.

One root cause (`payment_system_down`) is **deliberately left unscripted** for
`classify_issue_type`/`assess_urgency`, so the suite also exercises
`decision.fallback.apply_confidence_floor`'s deterministic-table fallback end to end, not
only the confident path — see `evals/README.md` and the per-case `decision_paths_exercised`
check in `evals/scenario_suite.py`.

Import as: ``from evals.fixtures import build_intent_rules, build_decision_rules``.
"""
from __future__ import annotations

import re
from typing import Any

from core_common.config import RoutingFile
from llm.scripted import ScriptedRule
from privacy.masking import mask_text

_NO_ACTION_INTENT: dict[str, str] = {
    "smalltalk": "smalltalk",
    "out_of_scope": "out_of_scope",
    "status_query": "status_query",
}


def _rule(text: str, intent: str, *, confidence: float = 0.97) -> ScriptedRule:
    masked = mask_text(text).masked
    return ScriptedRule(
        match=masked,
        exact=True,
        structured={
            "_IntentResult": {"value": intent, "confidence": confidence, "rationale": "eval fixture"}
        },
    )


def build_intent_rules(scenarios_doc: dict[str, Any], advisory_doc: dict[str, Any]) -> list[ScriptedRule]:
    rules: list[ScriptedRule] = []

    for scenario in scenarios_doc.get("scenarios", []):
        for message in scenario.get("messages", []):
            rules.append(_rule(message["text"], "problem_report"))

    for case in scenarios_doc.get("no_action_cases", []):
        intent = _NO_ACTION_INTENT.get(case["id"])
        if intent is None:
            raise ValueError(f"no_action_cases entry {case['id']!r} has no known intent mapping")
        rules.append(_rule(case["message"], intent))

    for profile in advisory_doc.get("profiles", []):
        conversation = profile.get("conversation_en") or []
        if conversation:
            rules.append(_rule(conversation[0], "advisory"))

    return rules


# Root causes `modes/action.py::handle_action` resolves through `_ROOT_CAUSE_ACTION` (every
# scenario in this dataset except `regional_outage`, which never reaches that branch).
# `payment_system_down` is intentionally excluded — see module docstring.
_SCRIPTED_ROOT_CAUSES: tuple[str, ...] = (
    "stuck_provisioning",
    "paid_not_active",
    "double_charge",
    "missed_installation",
)
DELIBERATELY_UNSCRIPTED_ROOT_CAUSE = "payment_system_down"


def build_decision_rules(routing_config: RoutingFile) -> list[ScriptedRule]:
    """`classify_issue_type`/`assess_urgency` (confidently scripted for every root cause in
    `_SCRIPTED_ROOT_CAUSES`) plus one defensive, documented-as-unreachable
    `choose_department` fixture. `routing_config.urgency_floor` supplies the urgency value
    so this never drifts out of sync with `config/tenants/netswift/routing.yaml`."""
    rules: list[ScriptedRule] = []
    for root_cause in _SCRIPTED_ROOT_CAUSES:
        urgency = routing_config.urgency_floor.get(root_cause, "NORMAL")
        pattern = rf"'root_cause':\s*'{re.escape(root_cause)}'"
        rules.append(
            ScriptedRule(
                match=pattern,
                structured={
                    "_IssueTypeResult": {"value": root_cause, "confidence": 0.93, "rationale": "eval fixture"},
                    "_UrgencyResult": {"value": urgency, "confidence": 0.93, "rationale": "eval fixture"},
                },
            )
        )

    # Not reachable by this dataset (see module docstring) — present only so a future
    # scenario without a policy `escalate_to` doesn't silently hit an unscripted-prompt
    # error instead of a clear fixture gap.
    rules.append(
        ScriptedRule(
            match=r"'scope':",
            structured={
                "_DepartmentResult": {
                    "value": "SUBSCRIPTION_OPS", "confidence": 0.9,
                    "rationale": "eval fixture (not reachable by the current dataset/policy.yaml)",
                }
            },
        )
    )
    return rules

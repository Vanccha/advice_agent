"""Agentic investigation (modes/investigation/): the second stage of DIAGNOSTIC when the
deterministic checklist finds no known fault. No network: ScriptedProvider plays the model."""
from __future__ import annotations

from typing import Any

import pytest

from audit.log import AuditLog
from core_common.types import Mode, StepType
from llm.base import AgentMessage, ProviderError, ToolStep
from llm.guard import MaskingProvider
from llm.scripted import ScriptedProvider, ScriptedRule
from modes.investigation import SUBMIT_FINDING
from modes.tests.conftest import build_orchestrator, make_gateway
from privacy.masking import PIILeakError

CUSTOMER = "NS-100001"
SLOW = "My internet is really slow in the evenings, it is a problem"
FOLLOW_UP = "It happens every evening around eight, on wifi and on cable"


def _intent(pattern: str, **kwargs: Any) -> ScriptedRule:
    return ScriptedRule(
        match=pattern,
        structured={"_IntentResult": {"value": "problem_report", "confidence": 0.95, "rationale": "t"}},
        **kwargs,
    )


def _submit(outcome: str, **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "outcome": outcome,
        "summary_en": "Evening slowdown reported; line and subscription look healthy.",
        "customer_reply_en": "Thanks for the details.",
        "clarifying_question_en": None,
        "suggested_department": "TECHNICAL_INFRA",
        "confidence": 0.85,
        "evidence": {"subscription_status": "active"},
    }
    args.update(overrides)
    return {"name": SUBMIT_FINDING, "arguments": args}


def _ask(question: str = "Does it affect wifi, cable or both?") -> dict[str, Any]:
    return _submit("needs_more_info", clarifying_question_en=question, suggested_department=None, confidence=0.7)


def _orch(tenant_config, session_factory, rules, *, calls: int = 14, overrides=None, provider=None):
    gateway = make_gateway(overrides, max_calls_per_turn=calls)
    provider = provider or ScriptedProvider(rules)
    return build_orchestrator(tenant_config, session_factory, gateway=gateway, provider=provider), gateway


def _audit(orch, conv_id: str):
    return AuditLog(orch.session_factory).timeline(conv_id)


def _created_tickets(gateway) -> list[dict[str, Any]]:
    return [args for name, args in gateway.calls_made if name == "create_structured_ticket"]


def test_needs_more_info_stays_diagnostic_and_next_message_continues(tenant_config, session_factory) -> None:
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[[{"name": "get_modem_status", "arguments": {"customer_no": CUSTOMER}}], [_ask()]],
        ),
        ScriptedRule(
            match=r"every evening",
            tool_script=[[_submit("escalate", summary_en="Evening congestion on wifi and cable; line healthy.")]],
        ),
    ]
    orch, gateway = _orch(tenant_config, session_factory, rules, overrides={"get_modem_status": {"online": True}})

    first = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert first.mode == Mode.DIAGNOSTIC.value
    assert "wifi, cable or both" in first.reply_en
    assert first.ticket_key is None
    assert "cannot see an obvious problem" not in first.reply_en
    assert ("get_modem_status", {"customer_no": CUSTOMER}) in gateway.calls_made
    assert not _created_tickets(gateway)

    second = orch.handle_message(conversation_id=first.conversation_id, customer_no=CUSTOMER, message=FOLLOW_UP)
    assert second.mode == Mode.ESCALATED.value
    assert second.ticket_key is not None
    [ticket] = _created_tickets(gateway)
    assert ticket["department"] == "TECHNICAL_INFRA"
    assert "Evening congestion" in ticket["body"]
    assert "slow in the evenings" in ticket["body"] or "every evening" in ticket["body"]
    assert ticket["evidence"]["record_ids"]["subscription_status"] == "active"

    entries = _audit(orch, first.conversation_id)
    steps = [e.summary for e in entries if e.step_type == StepType.DECISION_SERVICE.value and "investigation" in e.summary]
    assert any(s.startswith("investigation step 1") for s in steps)
    assert any("investigation finding: escalate" in s for s in steps)


def test_clarification_rounds_are_capped_then_a_ticket_is_created(tenant_config, session_factory) -> None:
    rules = [_intent(r"slow", tool_script=[[_ask()]])]
    orch, gateway = _orch(tenant_config, session_factory, rules)
    conv = None
    modes = []
    for text in (SLOW, "slow slow slow", "still slow"):
        result = orch.handle_message(conversation_id=conv, customer_no=CUSTOMER, message=text)
        conv = result.conversation_id
        modes.append(result.mode)
    assert modes == [Mode.DIAGNOSTIC.value, Mode.DIAGNOSTIC.value, Mode.ESCALATED.value]
    assert result.ticket_key is not None
    assert len(_created_tickets(gateway)) == 1


def test_resolved_info_answers_and_closes(tenant_config, session_factory) -> None:
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[[_submit("resolved_info", customer_reply_en="Your line is healthy and no incidents are open.")]],
        )
    ]
    orch, gateway = _orch(tenant_config, session_factory, rules)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.mode == Mode.CLOSING.value
    assert "line is healthy" in result.reply_en
    assert not _created_tickets(gateway)


def test_low_confidence_conclusion_goes_to_a_human(tenant_config, session_factory) -> None:
    rules = [_intent(r"slow in the evenings", tool_script=[[_submit("resolved_info", confidence=0.3)]])]
    orch, gateway = _orch(tenant_config, session_factory, rules)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.mode == Mode.ESCALATED.value
    assert result.ticket_key is not None
    assert len(_created_tickets(gateway)) == 1


def test_unknown_department_falls_back_to_issue_routing(tenant_config, session_factory) -> None:
    rules = [_intent(r"slow in the evenings", tool_script=[[_submit("escalate", suggested_department="WIZARDS")]])]
    orch, gateway = _orch(tenant_config, session_factory, rules)
    orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    [ticket] = _created_tickets(gateway)
    assert ticket["department"] == tenant_config.routing.issue_routing["other"]


def test_write_and_unknown_tools_are_refused_and_audited(tenant_config, session_factory) -> None:
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[
                [
                    {"name": "retry_provisioning_job", "arguments": {"customer_no": CUSTOMER, "job_id": 7}},
                    {"name": "apply_outage_credit", "arguments": {"customer_no": CUSTOMER}},
                    {"name": "totally_made_up", "arguments": {}},
                ],
                [_submit("escalate")],
            ],
        )
    ]
    orch, gateway = _orch(tenant_config, session_factory, rules)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    called = {name for name, _ in gateway.calls_made}
    assert not called & {"retry_provisioning_job", "apply_outage_credit", "totally_made_up"}
    refused = [e.summary for e in _audit(orch, result.conversation_id) if "refused tool" in e.summary]
    assert len(refused) == 3
    assert result.mode == Mode.ESCALATED.value


def test_customer_no_argument_is_forced_to_the_signed_in_customer(tenant_config, session_factory) -> None:
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[[{"name": "get_modem_status", "arguments": {"customer_no": "NS-999999"}}], [_ask()]],
        )
    ]
    orch, gateway = _orch(tenant_config, session_factory, rules, overrides={"get_modem_status": {"online": True}})
    orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    modem_calls = [args for name, args in gateway.calls_made if name == "get_modem_status"]
    assert modem_calls == [{"customer_no": CUSTOMER}]


class _RecordingProvider(ScriptedProvider):
    def __init__(self, rules):
        super().__init__(rules)
        self.seen: list[tuple[str, list[AgentMessage], list[str]]] = []

    def run_tool_step(self, *, system, messages, tools, temperature=0.0, max_tokens=None):
        self.seen.append((system, list(messages), [t.name for t in tools]))
        return super().run_tool_step(
            system=system, messages=messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )


def test_model_only_sees_masked_data_and_read_only_tools(tenant_config, session_factory) -> None:
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[[{"name": "find_customer", "arguments": {"customer_no": CUSTOMER}}], [_ask()]],
        )
    ]
    recorder = _RecordingProvider(rules)
    guarded = MaskingProvider(recorder)  # the production wrapper: would raise on any leak
    orch, gateway = _orch(tenant_config, session_factory, rules, provider=guarded)
    result = orch.handle_message(
        conversation_id=None, customer_no=CUSTOMER, message=SLOW + " call me on +44 7700 900133"
    )
    assert result.mode == Mode.DIAGNOSTIC.value

    second_step_messages = recorder.seen[1][1]
    tool_results = [m.content for m in second_step_messages if m.role == "tool"]
    assert tool_results, "the tool result must be fed back"
    blob = " ".join(tool_results) + " ".join(m.content for m in second_step_messages)
    for raw in ("adam.khan@example-mail.test", "+447700900133", "7700 900133", "Adam Khan"):
        assert raw not in blob
    offered = set(recorder.seen[0][2])
    assert SUBMIT_FINDING in offered
    assert not offered & {"retry_provisioning_job", "enqueue_provisioning_job", "apply_outage_credit",
                          "create_structured_ticket", "post_department_message", "add_ticket_comment",
                          "resend_activation_notification"}
    assert "find_customer" in offered


def test_loop_is_bounded_by_max_investigation_steps(tenant_config, session_factory) -> None:
    max_steps = tenant_config.policy.limits.max_investigation_steps
    looping = [[{"name": "get_service_health", "arguments": {}}]] * (max_steps + 3)
    rules = [_intent(r"slow in the evenings", tool_script=looping)]
    recorder = _RecordingProvider(rules)
    orch, _ = _orch(tenant_config, session_factory, rules, provider=recorder)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert len(recorder.seen) == max_steps + 1  # the last one is the forced "conclude" step
    assert recorder.seen[-1][2] == [SUBMIT_FINDING]
    assert result.mode == Mode.DIAGNOSTIC.value  # no finding -> ask for detail, do not close
    assert "obvious problem" not in result.reply_en


def test_tool_budget_exhaustion_finishes_with_what_it_has(tenant_config, session_factory) -> None:
    # The checklist alone spends the whole (tiny) budget, so the model's lookup is refused.
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[[{"name": "get_modem_status", "arguments": {}}], [_submit("resolved_info")]],
        )
    ]
    orch, gateway = _orch(
        tenant_config, session_factory, rules, calls=8, overrides={"get_modem_status": {"online": True}}
    )
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.mode == Mode.CLOSING.value  # concluded without further lookups
    assert not any(name == "get_modem_status" for name, _ in gateway.calls_made)
    assert any("tool budget exhausted" in e.summary for e in _audit(orch, result.conversation_id))


def test_unscripted_provider_falls_back_to_asking_for_detail(tenant_config, session_factory) -> None:
    rules = [_intent(r"slow in the evenings")]  # no tool_script
    orch, gateway = _orch(tenant_config, session_factory, rules)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.mode == Mode.DIAGNOSTIC.value
    assert "?" in result.reply_en
    assert "cannot see an obvious problem" not in result.reply_en
    assert not _created_tickets(gateway)


class _BrokenProvider(ScriptedProvider):
    def __init__(self, rules, error: Exception):
        super().__init__(rules)
        self._error = error

    def run_tool_step(self, **kwargs):
        raise self._error


@pytest.mark.parametrize("error", [PIILeakError("raw PII detected"), ProviderError("boom")])
def test_provider_failures_fall_back_without_breaking_the_turn(tenant_config, session_factory, error) -> None:
    provider = _BrokenProvider([_intent(r"slow in the evenings")], error)
    orch, _ = _orch(tenant_config, session_factory, [], provider=provider)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.mode == Mode.DIAGNOSTIC.value
    assert "?" in result.reply_en


def test_text_only_json_answer_is_accepted(tenant_config, session_factory) -> None:
    class _TextProvider(ScriptedProvider):
        def run_tool_step(self, **kwargs):
            return ToolStep(
                text='```json\n{"outcome":"resolved_info","summary_en":"ok","customer_reply_en":"All fine.","confidence":0.9}\n```'
            )

    provider = _TextProvider([_intent(r"slow in the evenings")])
    orch, _ = _orch(tenant_config, session_factory, [], provider=provider)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.mode == Mode.CLOSING.value
    assert result.reply_en == "All fine."


def test_known_root_causes_never_reach_the_investigation(tenant_config, session_factory) -> None:
    provider = _BrokenProvider(
        [_intent(r"not been switched on")], AssertionError("investigation must not run for a known root cause")
    )
    overrides = {
        "get_subscription_status": {
            "customer_no": CUSTOMER, "subscription_id": 42, "package_code": "FIBER_100_BASIC",
            "status": "provisioning", "monthly_price_gbp": 34.9, "region_code": "LDN-CAM",
        },
        "get_provisioning_status": {"job_id": 7, "status": "stuck", "attempt_count": 1, "is_stuck": True},
    }
    orch, gateway = _orch(tenant_config, session_factory, [], provider=provider, overrides=overrides)
    result = orch.handle_message(
        conversation_id=None, customer_no=CUSTOMER, message="My internet has not been switched on"
    )
    assert result.diagnosis["root_cause"] == "stuck_provisioning"
    assert any(name == "retry_provisioning_job" for name, _ in gateway.calls_made)


# -- follow-up fixes: no duplicated question, no checklist re-run, no repeated question ------


def test_question_is_not_duplicated_when_the_model_put_it_in_the_reply_too(tenant_config, session_factory) -> None:
    question = "Does it affect wifi, cable or both?"
    rules = [
        _intent(
            r"slow in the evenings",
            tool_script=[[_submit(
                "needs_more_info",
                customer_reply_en=f"Your line looks healthy. {question} Please tell me.",
                clarifying_question_en=question,
            )]],
        )
    ]
    orch, _ = _orch(tenant_config, session_factory, rules)
    result = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    assert result.reply_en.count("?") == 1
    assert result.reply_en.count("wifi, cable or both") == 1
    assert result.reply_en.startswith("Your line looks healthy.")


def test_follow_up_turn_reuses_the_checklist_diagnosis(tenant_config, session_factory) -> None:
    rules = [
        _intent(r"slow in the evenings", tool_script=[[_ask()]]),
        ScriptedRule(match=r"every evening", tool_script=[[_submit("escalate")]]),
    ]
    orch, gateway = _orch(tenant_config, session_factory, rules)
    first = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    checklist_calls = len(gateway.calls_made)
    assert checklist_calls >= 7
    second = orch.handle_message(conversation_id=first.conversation_id, customer_no=CUSTOMER, message=FOLLOW_UP)
    assert second.mode == Mode.ESCALATED.value
    later = [name for name, _ in gateway.calls_made[checklist_calls:]]
    assert "get_subscription_status" not in later and "get_service_health" not in later
    assert later == ["find_customer", "create_structured_ticket"] or later == ["create_structured_ticket"]


def test_continuation_prompt_lists_prior_questions_and_forbids_repeats(tenant_config, session_factory) -> None:
    rules = [_intent(r"slow", tool_script=[[_ask("Does it affect wifi, cable or both?")]])]
    recorder = _RecordingProvider(rules)
    orch, _ = _orch(tenant_config, session_factory, rules, provider=recorder)
    first = orch.handle_message(conversation_id=None, customer_no=CUSTOMER, message=SLOW)
    orch.handle_message(conversation_id=first.conversation_id, customer_no=CUSTOMER, message="slow on both wifi and cable")
    system_turn1, system_turn2 = recorder.seen[0][0], recorder.seen[1][0]
    assert "follow-up" not in system_turn1
    assert "NEVER ask a question already asked" in system_turn2
    assert "wifi, cable or both" in system_turn2
    assert "Your previous summary" in system_turn2
    assert "LAST round" not in system_turn2

    # third message is the last allowed round (max_clarification_rounds=2): conclude, don't ask
    orch.handle_message(conversation_id=first.conversation_id, customer_no=CUSTOMER, message="slow again")
    assert "LAST round" in recorder.seen[2][0]

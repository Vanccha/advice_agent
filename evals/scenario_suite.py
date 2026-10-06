"""The scenario suite (`evals/datasets/scenarios.yaml`, contracts §5): inject a chaos
scenario live against the company stack, send one Turkish user message through the real
`Orchestrator`, and assert every expectation key present in the case.

Injection strategy per scenario id (documented in `evals/README.md`):

- `stuck_provisioning` / `paid_not_active` / `double_charge` / `missed_installation`: each
  of the scenario's tone variants gets its *own* fresh chaos injection (a new customer/
  subscription every time) — this is what lets four independently-worded messages probe
  the same expectation without one turn's fix (e.g. `retry_provisioning_job`) silently
  changing the state the next tone variant would have seen.
- `regional_outage`: injected once (it affects a whole region), then each tone variant is
  sent as a different affected customer (cycled from the incident's own affected-customer
  list via `get_incident_detail`); a dedicated extra check sends two distinct affected
  customers and asserts the incident still has exactly one ticket.
- `payment_down`: injected once (a global PSP outage flag, no customer/region to pick), all
  tone variants are sent as the same fixed seeded customer.

`reset between cases` (contracts/task brief) means: once per scenario id, after every tone
variant for that id has been sent — never between tone variants of the *same* scenario
(each already has its own, independent chaos state), and once more at the very end of the
whole suite.

Import as: ``from evals.scenario_suite import run_scenario_suite``.
"""
from __future__ import annotations

from typing import Any

from evals import chaos_cli
from evals.chaos_cli import ChaosCliError
from evals.checks import BLAME_PHRASES_TR, CaseResult, contains_any, contains_none, error_case
from modes.tool_data import raw_payload

# Scenarios where each tone variant must inject its own fresh customer/subscription.
_FRESH_INJECTION_IDS = {"stuck_provisioning", "paid_not_active", "double_charge", "missed_installation"}

# A customer that always exists in the live seed (contracts §1.1: 200 seeded customers,
# NH-100001.. sequential) — used only where a scenario has no customer of its own to pick
# (`payment_down`'s global outage flag, and the no-action cases, which never touch chaos).
DEFAULT_CUSTOMER_NO = "NH-100001"


# --------------------------------------------------------------------------------------
# shared expectation checker
# --------------------------------------------------------------------------------------


def _apply_common_checks(
    case: CaseResult, harness: Any, turn: Any, conversation_id: str, expect: dict[str, Any],
    *, final_mode: str | None = None,
) -> None:
    diagnosis = turn.diagnosis or {}
    final_mode = final_mode if final_mode is not None else turn.mode

    if "final_mode" in expect:
        case.add("final_mode", final_mode in expect["final_mode"], expected=expect["final_mode"], actual=final_mode)

    if "issue_type" in expect:
        actual = diagnosis.get("root_cause")
        case.add("issue_type", actual == expect["issue_type"], expected=expect["issue_type"], actual=actual)

    if "scope" in expect:
        actual = diagnosis.get("scope")
        case.add("scope", actual == expect["scope"], expected=expect["scope"], actual=actual)

    if expect.get("incident_ref_required"):
        actual = diagnosis.get("incident_no")
        case.add("incident_ref_required", bool(actual), expected="non-null incident_no", actual=actual)

    cache: dict[str, list[dict[str, Any]]] = {}

    def _actions() -> list[dict[str, Any]]:
        if "actions" not in cache:
            cache["actions"] = harness.action_records_for(conversation_id)
        return cache["actions"]

    if "action_executed" in expect:
        wanted = expect["action_executed"]
        executed_names = [a["action_name"] for a in _actions() if a["executed"]]
        case.add("action_executed", wanted in executed_names, expected=wanted, actual=executed_names)

    if "action_executed_any" in expect:
        wanted = set(expect["action_executed_any"])
        executed_names = {a["action_name"] for a in _actions() if a["executed"]}
        case.add(
            "action_executed_any",
            bool(wanted & executed_names),
            expected=sorted(wanted),
            actual=sorted(executed_names),
        )

    if expect.get("must_not_execute_action"):
        executed_names = [a["action_name"] for a in _actions() if a["executed"]]
        case.add("must_not_execute_action", not executed_names, expected=[], actual=executed_names)

    if "policy_denied_action" in expect:
        wanted = expect["policy_denied_action"]
        denied = [a["action_name"] for a in _actions() if a["action_name"] == wanted and not a["policy_allowed"]]
        case.add(
            "policy_denied_action",
            bool(denied),
            expected=wanted,
            actual=[(a["action_name"], a["policy_allowed"]) for a in _actions()],
        )

    ticket_links = harness.ticket_links_for(conversation_id)

    if expect.get("must_not_create_ticket"):
        case.add("must_not_create_ticket", not ticket_links, expected=0, actual=len(ticket_links))

    needs_ticket_detail = (
        "ticket_department" in expect
        or expect.get("ticket_evidence_must_include_payment_ids")
        or expect.get("ticket_evidence_must_include_appointment_id")
        or expect.get("ticket_must_list_attempted_steps")
        or ("ticket_department_if_created" in expect and ticket_links)
    )
    ticket_record: dict[str, Any] | None = None
    if needs_ticket_detail and ticket_links:
        try:
            ticket_record = harness.get_ticket(ticket_links[-1].ticket_key)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            case.add("ticket_fetch", False, expected="fetchable ticket", actual=str(exc))

    if "ticket_must_create" in expect:
        case.add(
            "ticket_must_create",
            bool(ticket_links) == expect["ticket_must_create"],
            expected=expect["ticket_must_create"],
            actual=bool(ticket_links),
        )

    if "ticket_department" in expect:
        actual_dept = (ticket_record or {}).get("department") if ticket_record else (
            ticket_links[-1].department if ticket_links else None
        )
        case.add(
            "ticket_department", actual_dept == expect["ticket_department"],
            expected=expect["ticket_department"], actual=actual_dept,
        )

    if "ticket_department_if_created" in expect and ticket_links:
        actual_dept = (ticket_record or {}).get("department") if ticket_record else ticket_links[-1].department
        case.add(
            "ticket_department_if_created",
            actual_dept == expect["ticket_department_if_created"],
            expected=expect["ticket_department_if_created"], actual=actual_dept,
        )

    if expect.get("ticket_evidence_must_include_payment_ids"):
        record_ids = (ticket_record or {}).get("evidence") or {}
        ids = (record_ids.get("record_ids") or {}).get("payment_ids")
        case.add(
            "ticket_evidence_must_include_payment_ids",
            bool(ids) and len(ids) >= 2,
            expected=">=2 payment_ids", actual=ids,
        )

    if expect.get("ticket_evidence_must_include_appointment_id"):
        record_ids = (ticket_record or {}).get("evidence") or {}
        appt_id = (record_ids.get("record_ids") or {}).get("appointment_id")
        case.add(
            "ticket_evidence_must_include_appointment_id",
            appt_id is not None, expected="non-null appointment_id", actual=appt_id,
        )

    if expect.get("ticket_must_list_attempted_steps"):
        steps = (ticket_record or {}).get("attempted_steps") or []
        case.add("ticket_must_list_attempted_steps", bool(steps), expected="non-empty attempted_steps", actual=steps)

    if "reply_must_mention_any_tr" in expect:
        phrases = expect["reply_must_mention_any_tr"]
        case.add("reply_must_mention_any_tr", contains_any(turn.reply_tr, phrases), expected=phrases, actual=turn.reply_tr)

    if "reply_must_not_mention_any_tr" in expect:
        phrases = expect["reply_must_not_mention_any_tr"]
        case.add(
            "reply_must_not_mention_any_tr", contains_none(turn.reply_tr, phrases),
            expected=f"none of {phrases}", actual=turn.reply_tr,
        )

    if expect.get("must_not_blame_customer"):
        case.add(
            "must_not_blame_customer", contains_none(turn.reply_tr, BLAME_PHRASES_TR),
            expected=f"none of {BLAME_PHRASES_TR}", actual=turn.reply_tr,
        )

    # Informational only (always recorded as passed): which path `classify_issue_type`/
    # `assess_urgency`/`choose_department` took this turn — "confident" (the scripted
    # fixture's value was used) or "fallback" (confidence-0.0 -> deterministic table, see
    # `decision.fallback.apply_confidence_floor`). Lets a report reader see, per case,
    # whether the confident or the fallback path was exercised, without affecting PASS/FAIL.
    try:
        paths: dict[str, str] = {}
        for entry in harness.audit_timeline(conversation_id):
            if entry.step_type != "decision_service":
                continue
            for name in ("classify_issue_type", "assess_urgency", "choose_department"):
                if entry.summary.startswith(name):
                    paths[name] = "fallback" if (entry.evidence or {}).get("escalated") else "confident"
        if paths:
            case.add("decision_paths_exercised", True, expected="n/a (informational)", actual=paths)
    except Exception:  # noqa: BLE001 - informational only, never fails a case
        pass


def _safe_reset(results: list[CaseResult], case_id: str) -> None:
    try:
        chaos_cli.reset()
    except ChaosCliError as exc:
        results.append(error_case("scenarios", case_id, "reset", f"chaos reset failed: {exc}"))


# --------------------------------------------------------------------------------------
# per-scenario-id injection strategies
# --------------------------------------------------------------------------------------


def _run_fresh_injection_scenario(harness: Any, scenario: dict[str, Any], results: list[CaseResult]) -> None:
    case_id = scenario["id"]
    chaos_name = scenario["chaos"]
    expect = scenario["expect"]

    for message in scenario.get("messages", []):
        tone = message.get("tone")
        try:
            outcome = chaos_cli.inject(chaos_name)
        except ChaosCliError as exc:
            results.append(error_case("scenarios", case_id, tone, f"chaos injection failed: {exc}"))
            continue

        customer_no = (outcome.get("picked") or {}).get("customer_no")
        if not customer_no:
            results.append(error_case("scenarios", case_id, tone, f"chaos outcome had no customer_no: {outcome}"))
            continue

        conv_id = harness.new_conversation_id(f"{case_id}-{tone}")
        case = CaseResult(suite="scenarios", case_id=case_id, variant=tone, conversation_id=conv_id)
        try:
            turn = harness.send(conv_id, customer_no, message["text"])
        except Exception as exc:  # noqa: BLE001
            case.status = "ERROR"
            case.message = f"orchestrator call failed: {exc}"
            results.append(case)
            continue
        case.reply_tr = turn.reply_tr
        _apply_common_checks(case, harness, turn, conv_id, expect)
        results.append(case.finalize())

    _safe_reset(results, case_id)


def _run_regional_outage(harness: Any, scenario: dict[str, Any], results: list[CaseResult]) -> None:
    case_id = scenario["id"]
    expect = scenario["expect"]

    try:
        outcome = chaos_cli.inject("regional_outage")
    except ChaosCliError as exc:
        results.append(error_case("scenarios", case_id, None, f"chaos injection failed: {exc}"))
        return

    incident_no = (outcome.get("picked") or {}).get("incident_no")
    if not incident_no:
        results.append(error_case("scenarios", case_id, None, f"chaos outcome had no incident_no: {outcome}"))
        _safe_reset(results, case_id)
        return

    try:
        detail = raw_payload(harness.call_tool("get_incident_detail", {"incident_no": incident_no})) or {}
        affected = (detail.get("incident") or {}).get("affected_subscriptions") or []
        customer_pool: list[str] = []
        for row in affected:
            cno = row.get("customer_no")
            if cno and cno not in customer_pool:
                customer_pool.append(cno)
    except Exception as exc:  # noqa: BLE001
        results.append(error_case("scenarios", case_id, None, f"could not fetch affected customers: {exc}"))
        _safe_reset(results, case_id)
        return

    if not customer_pool:
        results.append(error_case("scenarios", case_id, None, "regional_outage injected but no affected customers found"))
        _safe_reset(results, case_id)
        return

    messages = scenario.get("messages", [])
    for i, message in enumerate(messages):
        tone = message.get("tone")
        customer_no = customer_pool[i % len(customer_pool)]
        conv_id = harness.new_conversation_id(f"{case_id}-{tone}")
        case = CaseResult(suite="scenarios", case_id=case_id, variant=tone, conversation_id=conv_id)
        try:
            turn = harness.send(conv_id, customer_no, message["text"])
        except Exception as exc:  # noqa: BLE001
            case.status = "ERROR"
            case.message = f"orchestrator call failed: {exc}"
            results.append(case)
            continue
        case.reply_tr = turn.reply_tr
        # The incident-attach + (confirmation-required) goodwill-credit offer both happen
        # in this same first turn (modes/action.py), so a first-time affected customer
        # legitimately pauses at AWAITING_APPROVAL — not a terminal mode the dataset's
        # `final_mode` list names. Resolve it (denied: no real monetary side effect against
        # the live payment system) so `final_mode` reflects the conversation's true end
        # state, exactly as a real chat session would continue past that prompt.
        final_mode = turn.mode
        if turn.mode == "AWAITING_APPROVAL" and turn.approval_id:
            try:
                resolved = harness.resolve_approval(turn.approval_id, granted=False)
                final_mode = resolved.mode
            except Exception as exc:  # noqa: BLE001
                case.message = (case.message + "; " if case.message else "") + f"approval resolution failed: {exc}"
        _apply_common_checks(case, harness, turn, conv_id, expect, final_mode=final_mode)
        results.append(case.finalize())

    # Explicit "two different customers -> one ticket" check (contracts §5 scenario c /
    # task brief). Uses two distinct affected customers when the region has them; if the
    # live stack only produced one affected customer this run, we say so rather than
    # quietly re-using the same one and calling it a two-customer test.
    dedup = CaseResult(suite="scenarios", case_id=case_id, variant="max_tickets_dedup")
    two = customer_pool[:2] if len(customer_pool) >= 2 else customer_pool * 2
    if len(customer_pool) < 2:
        dedup.message = "only one affected customer available in this region; reused it twice"
    try:
        sample_text = messages[0]["text"] if messages else "Sorun yaşıyorum."
        for idx, cno in enumerate(two):
            conv_id = harness.new_conversation_id(f"{case_id}-dedup-{idx}")
            harness.send(conv_id, cno, sample_text)
        tickets = harness.tickets_for_incident(incident_no)
        max_allowed = expect.get("max_tickets_for_incident", 1)
        dedup.add(
            "max_tickets_for_incident", len(tickets) <= max_allowed,
            expected=f"<= {max_allowed}", actual=len(tickets),
        )
    except Exception as exc:  # noqa: BLE001
        dedup.status = "ERROR"
        dedup.message = (dedup.message + "; " if dedup.message else "") + f"ticket count check failed: {exc}"
    results.append(dedup.finalize())

    _safe_reset(results, case_id)


def _run_payment_down(harness: Any, scenario: dict[str, Any], results: list[CaseResult], default_customer: str) -> None:
    case_id = scenario["id"]
    expect = scenario["expect"]

    try:
        chaos_cli.inject("payment_down")
    except ChaosCliError as exc:
        results.append(error_case("scenarios", case_id, None, f"chaos injection failed: {exc}"))
        return

    for message in scenario.get("messages", []):
        tone = message.get("tone")
        conv_id = harness.new_conversation_id(f"{case_id}-{tone}")
        case = CaseResult(suite="scenarios", case_id=case_id, variant=tone, conversation_id=conv_id)
        try:
            turn = harness.send(conv_id, default_customer, message["text"])
        except Exception as exc:  # noqa: BLE001
            case.status = "ERROR"
            case.message = f"orchestrator call failed: {exc}"
            results.append(case)
            continue
        case.reply_tr = turn.reply_tr
        _apply_common_checks(case, harness, turn, conv_id, expect)
        results.append(case.finalize())

    _safe_reset(results, case_id)


def _run_no_action_case(harness: Any, case: dict[str, Any], results: list[CaseResult], default_customer: str) -> None:
    case_id = case["id"]
    conv_id = harness.new_conversation_id(f"noaction-{case_id}")
    result = CaseResult(suite="scenarios", case_id=case_id, conversation_id=conv_id)
    try:
        turn = harness.send(conv_id, default_customer, case["message"])
    except Exception as exc:  # noqa: BLE001
        result.status = "ERROR"
        result.message = f"orchestrator call failed: {exc}"
        results.append(result)
        return
    result.reply_tr = turn.reply_tr
    _apply_common_checks(result, harness, turn, conv_id, case["expect"])
    results.append(result.finalize())


# --------------------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------------------


def run_scenario_suite(
    harness: Any,
    scenarios_doc: dict[str, Any],
    *,
    only_case: str | None = None,
    no_chaos: bool = False,
    default_customer: str = DEFAULT_CUSTOMER_NO,
) -> list[CaseResult]:
    results: list[CaseResult] = []

    for scenario in scenarios_doc.get("scenarios", []):
        case_id = scenario["id"]
        if only_case and case_id != only_case:
            continue
        if no_chaos:
            for message in scenario.get("messages", []):
                results.append(
                    error_case("scenarios", case_id, message.get("tone"), "chaos injection skipped (--no-chaos)")
                )
            continue

        if case_id == "regional_outage":
            _run_regional_outage(harness, scenario, results)
        elif case_id == "payment_down":
            _run_payment_down(harness, scenario, results, default_customer)
        elif case_id in _FRESH_INJECTION_IDS:
            _run_fresh_injection_scenario(harness, scenario, results)
        else:
            results.append(error_case("scenarios", case_id, None, f"no injection strategy wired for id {case_id!r}"))

    for case in scenarios_doc.get("no_action_cases", []):
        if only_case and case["id"] != only_case:
            continue
        _run_no_action_case(harness, case, results, default_customer)

    return results

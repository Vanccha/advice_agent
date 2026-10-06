"""ACTION mode (contracts §4.3/§4.6): map a `Diagnosis` to a candidate action and run it
**only** through `policy.executor.ActionExecutor` — never call a mutating tool directly.

- `PolicyDenied` -> build a structured ticket for `decision.escalate_to`, mode ESCALATED.
- `ConfirmationRequired` -> persist a `pending_approvals` row, mode AWAITING_APPROVAL.
- success -> inform the customer what was fixed, mode CLOSING.

Issue type, urgency and (when policy does not already fix it) department are real
`DecisionService` calls now (contracts §4.5) — `_ROOT_CAUSE_ACTION` below is the
**fallback** table for when the service is unsure, wrong, or unavailable, not the primary
source of truth it used to be. See `_decide_issue_type`/`_decide_priority`/
`_decide_department`.

Import as: ``from modes.action import handle_action, ActionStepResult``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core_common.models import PendingApproval
from core_common.db import session_scope
from core_common.types import Department, Diagnosis, IssueType, Mode, Priority, StepType
from decision.base import DecisionContext
from decision.fallback import PRIORITY_ORDER, apply_confidence_floor
from modes import diagnostic
from modes.context import TurnContext
from modes.incident_policy import ensure_incident_ticket
from modes.ticket_links import record_ticket_link
from observability.tracing import record_decision
from policy.executor import ConfirmationRequired, PolicyDenied
from tickets.builder import build_structured_ticket

# root_cause -> (policy action name, deterministic IssueType, deterministic Priority).
# The action name is always deterministic (no `DecisionService` call decides *what to try*,
# only how to classify/route/escalate it). The IssueType/Priority here are now only the
# **fallback** values `_decide_issue_type`/`_decide_priority` use when the DecisionService
# itself is unsure, wrong, or unavailable (contracts §4.5).
_ROOT_CAUSE_ACTION: dict[str, tuple[str, IssueType, Priority]] = {
    diagnostic.ROOT_CAUSE_STUCK_PROVISIONING: (
        "retry_provisioning_job", IssueType.STUCK_PROVISIONING, Priority.NORMAL,
    ),
    diagnostic.ROOT_CAUSE_PAID_NOT_ACTIVE: (
        "enqueue_provisioning_job", IssueType.PAID_NOT_ACTIVE, Priority.NORMAL,
    ),
    diagnostic.ROOT_CAUSE_DOUBLE_CHARGE: (
        "issue_refund", IssueType.DOUBLE_CHARGE, Priority.HIGH,
    ),
    diagnostic.ROOT_CAUSE_MISSED_INSTALLATION: (
        "reschedule_installation", IssueType.MISSED_INSTALLATION, Priority.NORMAL,
    ),
    diagnostic.ROOT_CAUSE_PAYMENT_SYSTEM_DOWN: (
        "repair_infrastructure", IssueType.PAYMENT_SYSTEM_DOWN, Priority.URGENT,
    ),
}

_SUCCESS_REPLY_EN = {
    "retry_provisioning_job": (
        "I found that your setup job had got stuck, and I have restarted it just now. I "
        "expect it to finish within a few minutes."
    ),
    "enqueue_provisioning_job": (
        "I can see your payment was received but the setup process never started; I have "
        "now queued your setup job."
    ),
}


@dataclass
class ActionStepResult:
    reply_en: str
    next_mode: Mode
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)


def _department_en(ctx: TurnContext, department: Department | str | None) -> str:
    if department is None:
        return "the relevant team"
    code = department.value if isinstance(department, Department) else str(department)
    dept_cfg = ctx.tenant_config.departments.get(code)
    return dept_cfg.display_name_en if dept_cfg else code


def _build_policy_context(ctx: TurnContext, diagnosis: Diagnosis) -> dict[str, Any]:
    evidence = diagnosis.evidence
    context: dict[str, Any] = {
        "conversation_id": ctx.conversation_id,
        "tenant": ctx.tenant,
        "job": {
            "status": evidence.get("job_status"),
            "attempt_count": evidence.get("job_attempt_count"),
        },
        "subscription": {"status": evidence.get("subscription_status")},
        "payment": {"status": evidence.get("payment_status")},
        "incident": {"exists": diagnosis.incident_no is not None},
        "credit": {"existing_count_30d": 0},
        "amount_gbp": 5.0,
    }
    return context


def _build_params(root_cause: str, diagnosis: Diagnosis, customer_no: str) -> dict[str, Any]:
    evidence = diagnosis.evidence
    base = {"customer_no": customer_no, "subscription_id": evidence.get("subscription_id")}
    if root_cause == diagnostic.ROOT_CAUSE_STUCK_PROVISIONING:
        base["job_id"] = evidence.get("job_id")
    if root_cause == diagnostic.ROOT_CAUSE_REGIONAL_OUTAGE:
        base["amount_gbp"] = 5.0
        base["reason"] = f"Regional outage compensation ({diagnosis.incident_no})"
    return base


def _decision_context(ctx: TurnContext, diagnosis: Diagnosis) -> DecisionContext:
    masked_facts = {k: v for k, v in diagnosis.evidence.items() if k != "queried_sources"}
    return DecisionContext(
        conversation_id=ctx.conversation_id,
        tenant=ctx.tenant,
        history=ctx.history,
        masked_customer_facts=masked_facts,
        diagnosis=diagnosis,
        tenant_config_slice={
            "known_issue_types": [t.value for t in IssueType],
            "known_departments": [d.value for d in Department],
            "known_priorities": [p.value for p in Priority],
        },
    )


# What the customer is actually told we found, before being told it was handed over.
# "I am not authorised, I forwarded it" on its own leaves the customer none the wiser —
# worse, during a payment outage it lets them assume their card is at fault.
_FINDING_EN: dict[str, str] = {
    "stuck_provisioning": "I found that your setup (provisioning) job has got stuck.",
    "paid_not_active": (
        "I found that your payment was received, but your subscription has not been activated."
    ),
    "double_charge": "I found that the same amount was taken from your account twice.",
    "missed_installation": "I found that your installation appointment did not take place.",
    "regional_outage": (
        "I found an ongoing infrastructure fault in your area; the problem is not specific to you."
    ),
    "payment_system_down": (
        "Our payment system is temporarily unavailable. The problem is not with your card or "
        "your device; you can try your payment again once the system is back to normal."
    ),
    "no_issue_found": "I could not pin down the cause of the problem in your records.",
    "customer_not_found": "I could not reach your records.",
}


# The department reads these fields, so they are plain-language and specific. A subject of
# "double_charge — NS-100039" and a next step that merely repeats the refusal both make a
# human re-do the work the assistant already did.
_TICKET_SUBJECT_EN: dict[str, str] = {
    "double_charge": "Double charge — {customer_no}",
    "stuck_provisioning": "Provisioning job stuck — {customer_no}",
    "paid_not_active": "Payment received, subscription not active — {customer_no}",
    "regional_outage": "Regional infrastructure fault — {customer_no}",
    "missed_installation": "Installation appointment missed — {customer_no}",
    "payment_system_down": "Payment system unavailable — {customer_no}",
    "refund_request": "Refund request — {customer_no}",
    "plan_change_request": "Package change request — {customer_no}",
    "infrastructure_repair": "Infrastructure repair needed — {customer_no}",
}

_NEXT_STEP_EN: dict[str, str] = {
    "double_charge": "Approve the refund of the duplicate payment (payment records are in the evidence).",
    "missed_installation": "Contact the customer and book a new installation appointment.",
    "regional_outage": "Update the resolution time on the incident record and keep the customer informed.",
    "paid_not_active": "Activate the subscription manually or check the provisioning process.",
    "stuck_provisioning": "Check the provisioning infrastructure; restarting the job did not help.",
    "payment_system_down": "The payment infrastructure team should take over; get back to the customer.",
    "plan_change_request": "Work out the package change and any early termination fee, and tell the customer.",
    "infrastructure_repair": "Dispatch the field/network team.",
}

_ID_EVIDENCE_KEYS = ("record_ids", "observations", "error_codes", "queried_sources")


def _evidence_record_ids(evidence: dict[str, Any]) -> dict[str, Any]:
    """Ids only — anything that is not an identifier belongs in its own evidence field."""
    nested = evidence.get("record_ids")
    collected: dict[str, Any] = dict(nested) if isinstance(nested, dict) else {}
    for key, value in evidence.items():
        if key in _ID_EVIDENCE_KEYS:
            continue
        collected[key] = value
    return {
        key: value
        for key, value in collected.items()
        if key not in ("observations", "error_codes")
    }


def _evidence_list(evidence: dict[str, Any], key: str) -> list[Any]:
    """Merge a list-valued evidence field whether it sits at the top level or under
    `record_ids`, since diagnostic steps write it in both places."""
    merged: list[Any] = []
    for source in (evidence, evidence.get("record_ids") or {}):
        if isinstance(source, dict):
            for item in source.get(key) or []:
                if item not in merged:
                    merged.append(item)
    return merged


def _finding_en(root_cause: str) -> str:
    return _FINDING_EN.get(root_cause, "I have looked into your situation.")


def _decide_issue_type(
    ctx: TurnContext, diagnosis: Diagnosis, deterministic_issue_type: IssueType
) -> IssueType:
    """contracts §4.5: `classify_issue_type` is a real `DecisionService` call now.
    `deterministic_issue_type` (the `_ROOT_CAUSE_ACTION` table below) is used instead
    whenever the service's answer is below `policy.yaml: decision.min_confidence`, out of
    enum, or the provider call itself failed — `LLMStructuredDecisionService` turns all
    three of those into a confidence-0.0 `Decision`, so a single confidence check covers
    every case and a provider failure can never break the turn."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    decision = ctx.decision_service.classify_issue_type(_decision_context(ctx, diagnosis))
    record_decision(
        "classify_issue_type",
        value=decision.value.value,
        confidence=decision.confidence,
        rationale=decision.rationale,
        model=decision.model,
    )
    fallback = apply_confidence_floor(decision, min_confidence, ctx.tenant_config.routing)
    issue_type = deterministic_issue_type if fallback.escalated else fallback.decision.value

    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.DECISION_SERVICE,
        f"classify_issue_type -> '{issue_type.value}'"
        + (
            " (low-confidence/invalid decision-service answer; deterministic root-cause "
            "table used instead)"
            if fallback.escalated
            else ""
        ),
        fallback.decision.rationale,
        {
            "confidence": decision.confidence,
            "escalated": fallback.escalated,
            "model_value": decision.value.value,
            "used_value": issue_type.value,
        },
        tenant=ctx.tenant,
    )
    return issue_type


def _decide_priority(
    ctx: TurnContext,
    diagnosis: Diagnosis,
    issue_type: IssueType,
    deterministic_priority: Priority,
) -> Priority:
    """contracts §4.5: `assess_urgency` is a real `DecisionService` call now.
    `deterministic_priority` (the `_ROOT_CAUSE_ACTION` table) is used whenever the service's
    answer is below the confidence floor. Independently of confidence, the result may never
    rank below `routing.yaml: urgency_floor[issue_type]` — the service may only ever raise
    urgency for an issue type, never lower it (contracts §4.5/§4.6)."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    routing = ctx.tenant_config.routing
    decision = ctx.decision_service.assess_urgency(_decision_context(ctx, diagnosis))
    record_decision(
        "assess_urgency",
        value=decision.value.value,
        confidence=decision.confidence,
        rationale=decision.rationale,
        model=decision.model,
    )
    fallback = apply_confidence_floor(decision, min_confidence, routing, issue_type=issue_type.value)
    priority = deterministic_priority if decision.confidence < min_confidence else fallback.decision.value

    floor_name = routing.urgency_floor.get(issue_type.value)
    if floor_name is not None:
        floor_priority = Priority(floor_name)
        if PRIORITY_ORDER[floor_priority.value] > PRIORITY_ORDER[priority.value]:
            priority = floor_priority

    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.DECISION_SERVICE,
        f"assess_urgency -> '{priority.value}'"
        + (
            " (low-confidence decision-service answer; deterministic root-cause priority "
            "used instead)"
            if fallback.escalated
            else ""
        ),
        fallback.decision.rationale,
        {
            "confidence": decision.confidence,
            "escalated": fallback.escalated,
            "model_value": decision.value.value,
            "used_value": priority.value,
        },
        tenant=ctx.tenant,
    )
    return priority


def _decide_department(ctx: TurnContext, diagnosis: Diagnosis, issue_type: IssueType) -> Department:
    """contracts §4.5: `choose_department` is a real `DecisionService` call now. Its result
    is used as the ticket's department whenever `policy.yaml` does not already fix one via
    `escalate_to` for the denied action (see the call site in `handle_action`) — policy's own
    `escalate_to` remains authoritative when present, since which department has *authority*
    over an action is a policy decision, never the model's (contracts §4.6), while this call
    only decides *routing* for the (rarer) case policy leaves open."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    decision = ctx.decision_service.choose_department(_decision_context(ctx, diagnosis))
    record_decision(
        "choose_department",
        value=decision.value.value,
        confidence=decision.confidence,
        rationale=decision.rationale,
        model=decision.model,
    )
    fallback = apply_confidence_floor(
        decision, min_confidence, ctx.tenant_config.routing, issue_type=issue_type.value
    )
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.DECISION_SERVICE,
        f"choose_department -> '{fallback.decision.value.value}'"
        + (
            " (low-confidence decision-service answer; routed via issue_routing table)"
            if fallback.escalated
            else ""
        ),
        fallback.decision.rationale,
        {"confidence": fallback.decision.confidence, "escalated": fallback.escalated},
        tenant=ctx.tenant,
    )
    return fallback.decision.value


def _escalate_with_ticket(
    ctx: TurnContext,
    *,
    department: Department,
    issue_type: IssueType,
    priority: Priority,
    diagnosis: Diagnosis,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
    attempted_action: str,
    blocked_reason_en: str,
) -> ActionStepResult:
    evidence = diagnosis.evidence
    ticket = build_structured_ticket(
        conversation_id=ctx.conversation_id,
        department=department,
        issue_type=issue_type,
        priority=priority,
        subject_en=_TICKET_SUBJECT_EN.get(
            issue_type.value, f"Support request ({issue_type.value}) — {customer_no}"
        ).format(customer_no=customer_no),
        body_en=(
            f"Diagnosis: {diagnosis.root_cause}. For customer {customer_no}, the "
            f"'{attempted_action}' action was blocked by policy: {blocked_reason_en} "
            "Please review it manually."
        ),
        requester_customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
        suggested_next_step_en=_NEXT_STEP_EN.get(issue_type.value, blocked_reason_en),
        urgency_reason_en=f"Diagnosis confidence: {diagnosis.confidence:.2f}",
        evidence_record_ids=_evidence_record_ids(evidence),
        evidence_observations=_evidence_list(evidence, "observations"),
        evidence_error_codes=_evidence_list(evidence, "error_codes"),
        evidence_queried_sources=evidence.get("queried_sources", []),
        attempted_steps=[
            {"step": f"diagnosis:{diagnosis.root_cause}", "result": "established", "outcome": "info"},
            {"step": f"policy_check:{attempted_action}", "result": blocked_reason_en, "outcome": "blocked"},
        ],
        affected_customers=[customer_no],
        incident_ref=diagnosis.incident_no,
    )
    ticket_ref = ctx.ticket_service.create(ticket)
    record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=department.value)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.TICKET_CREATED,
        f"created ticket {ticket_ref.ticket_key} for department {department.value}",
        blocked_reason_en,
        {"ticket_key": ticket_ref.ticket_key, "department": department.value},
        tenant=ctx.tenant,
    )
    dept_en = _department_en(ctx, department)
    reply_en = (
        f"{_finding_en(diagnosis.root_cause)} I am not authorised to complete this myself, "
        f"so I have passed your request to the {dept_en} team. "
        f"Your reference number is {ticket_ref.ticket_key}."
    )
    return ActionStepResult(
        reply_en=reply_en,
        next_mode=Mode.ESCALATED,
        ticket_key=ticket_ref.ticket_key,
        actions=[
            {
                "label_en": f"Ticket created: {department.value}",
                "action_name": attempted_action,
                "executed": False,
                "policy_allowed": False,
                "escalated_to": department.value,
                "ticket_key": ticket_ref.ticket_key,
            }
        ],
    )


def handle_action(
    ctx: TurnContext,
    *,
    diagnosis: Diagnosis,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
) -> ActionStepResult:
    root_cause = diagnosis.root_cause

    if root_cause == diagnostic.ROOT_CAUSE_NO_ISSUE_FOUND:
        return ActionStepResult(
            reply_en=(
                "I have checked your account and subscription, and I cannot see an obvious "
                "problem right now. If it carries on, please share a few more details."
            ),
            next_mode=Mode.CLOSING,
        )
    if root_cause == diagnostic.ROOT_CAUSE_CUSTOMER_NOT_FOUND:
        return ActionStepResult(
            reply_en="I could not find your customer number; please check it and try again.",
            next_mode=Mode.CLOSING,
        )

    if root_cause == diagnostic.ROOT_CAUSE_REGIONAL_OUTAGE and diagnosis.incident_no:
        incident_ticket = ensure_incident_ticket(
            ctx,
            incident_no=diagnosis.incident_no,
            customer_no=customer_no,
            requester_name=requester_name,
            requester_contact=requester_contact,
        )
        # Offer the (confirmation-required, irreversible) outage credit on top of the
        # incident attachment — two independent concerns, both handled this turn.
        action_name = "apply_outage_credit"
        policy_context = _build_policy_context(ctx, diagnosis)
        params = _build_params(root_cause, diagnosis, customer_no)
        try:
            ctx.action_executor.execute("apply_outage_credit", params, policy_context, confirmed=False)
        except ConfirmationRequired as exc:
            approval_id = _persist_pending_approval(
                ctx, action_name="apply_outage_credit", params=params, policy_context=policy_context,
                prompt_en=exc.decision.reason_en or "This action needs your approval.",
            )
            reply_en = (
                f"There is an open fault record for your area ({diagnosis.incident_no}), and I "
                f"have added your request to ticket {incident_ticket.ticket_key}. I can apply a "
                "goodwill credit of up to £5 to your account for the outage, but as this cannot "
                "be undone I need your approval first. Do you approve?"
            )
            return ActionStepResult(
                reply_en=reply_en,
                next_mode=Mode.AWAITING_APPROVAL,
                ticket_key=incident_ticket.ticket_key,
                requires_approval=True,
                approval_id=approval_id,
                actions=[
                    {
                        "label_en": "Awaiting approval: apply_outage_credit",
                        "action_name": "apply_outage_credit",
                        "executed": False,
                        "policy_allowed": True,
                        "awaiting_confirmation": True,
                    }
                ],
            )
        except PolicyDenied as exc:
            reply_en = (
                f"There is an open fault record for your area ({diagnosis.incident_no}), and I "
                f"have added your request to ticket {incident_ticket.ticket_key}. I cannot apply "
                f"a credit for this outage: {exc.decision.reason_en}"
            )
            return ActionStepResult(
                reply_en=reply_en, next_mode=Mode.ESCALATED, ticket_key=incident_ticket.ticket_key,
            )
        else:
            # allowed without confirmation (shouldn't happen per policy.yaml, but handle it)
            reply_en = (
                f"There is an open fault record for your area ({diagnosis.incident_no}), and I "
                f"have added your request to ticket {incident_ticket.ticket_key}."
            )
            return ActionStepResult(reply_en=reply_en, next_mode=Mode.CLOSING, ticket_key=incident_ticket.ticket_key)

    mapping = _ROOT_CAUSE_ACTION.get(root_cause)
    if mapping is None:
        return ActionStepResult(
            reply_en=(
                "I have looked into it, but there is no step I can take automatically; if you "
                "like, I can pass you to an agent."
            ),
            next_mode=Mode.CLOSING,
        )

    action_name, deterministic_issue_type, deterministic_priority = mapping
    issue_type = _decide_issue_type(ctx, diagnosis, deterministic_issue_type)
    priority = _decide_priority(ctx, diagnosis, issue_type, deterministic_priority)
    policy_context = _build_policy_context(ctx, diagnosis)
    params = _build_params(root_cause, diagnosis, customer_no)

    try:
        ctx.action_executor.execute(action_name, params, policy_context, confirmed=False)
    except PolicyDenied as exc:
        # policy's own `escalate_to` is authoritative when set (contracts §4.6: the model
        # never decides its own authority) — `choose_department` only fills the gap for an
        # action policy.yaml leaves without one, with its own confidence floor/fallback.
        department = exc.decision.escalate_to or _decide_department(ctx, diagnosis, issue_type)
        return _escalate_with_ticket(
            ctx,
            department=department,
            issue_type=issue_type,
            priority=priority,
            diagnosis=diagnosis,
            customer_no=customer_no,
            requester_name=requester_name,
            requester_contact=requester_contact,
            attempted_action=action_name,
            blocked_reason_en=exc.decision.reason_en or "blocked by policy",
        )
    except ConfirmationRequired as exc:
        approval_id = _persist_pending_approval(
            ctx, action_name=action_name, params=params, policy_context=policy_context,
            prompt_en=exc.decision.reason_en or "This action needs your approval.",
        )
        return ActionStepResult(
            reply_en=f"{exc.decision.reason_en} Do you approve?",
            next_mode=Mode.AWAITING_APPROVAL,
            requires_approval=True,
            approval_id=approval_id,
            actions=[
                {
                    "label_en": f"Awaiting approval: {action_name}",
                    "action_name": action_name,
                    "executed": False,
                    "policy_allowed": True,
                    "awaiting_confirmation": True,
                }
            ],
        )
    else:
        reply_en = _SUCCESS_REPLY_EN.get(
            action_name, f"I have successfully carried out {action_name}."
        )
        return ActionStepResult(
            reply_en=reply_en,
            next_mode=Mode.CLOSING,
            actions=[
                {
                    "label_en": f"Action applied: {action_name}",
                    "action_name": action_name,
                    "executed": True,
                    "policy_allowed": True,
                }
            ],
        )


def _persist_pending_approval(
    ctx: TurnContext,
    *,
    action_name: str,
    params: dict[str, Any],
    policy_context: dict[str, Any],
    prompt_en: str,
) -> str:
    with session_scope(ctx.audit_log.session_factory) as session:
        row = PendingApproval(
            conversation_id=ctx.conversation_id,
            action_name=action_name,
            params={"action_params": params, "policy_context": policy_context},
            prompt_en=prompt_en,
            status="pending",
        )
        session.add(row)
        session.flush()
        approval_id = str(row.id)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.APPROVAL_REQUESTED,
        f"approval #{approval_id} requested for action '{action_name}'",
        prompt_en,
        {"action_name": action_name},
        tenant=ctx.tenant,
    )
    return approval_id

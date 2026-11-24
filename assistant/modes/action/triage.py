"""Triage: issue type, priority and department for an escalation, via the DecisionService.

Where this sits: `modes.action`. `known_causes.py` calls `compute_triage` once per
escalation, then the three `decide_*` helpers turn that one answer into trusted values.

Key ideas:
- The model advises, deterministic rules decide: below `policy.yaml: decision.min_confidence`
  (or on an invalid answer or provider failure) the root-cause table's value is used
  instead, so a provider failure can never break a turn (contracts §4.5).
- Priority may be raised by the model but never lowered below
  `routing.yaml: urgency_floor[issue_type]`.
- Department: policy's own `escalate_to` is authoritative; `decide_department` only fills
  the gap policy leaves open (contracts §4.6).
- Every decision is audited with both the model's value and the value actually used.

Read next: modes/action/escalation.py, decision/fallback.py.
See: docs/contracts.md §4.5, §4.6.
"""
from __future__ import annotations

from core_common.types import Department, Diagnosis, IssueType, Priority, StepType, Triage
from decision.base import DecisionContext, triage_via_individual_calls
from decision.fallback import PRIORITY_ORDER, apply_confidence_floor
from modes.context import TurnContext
from observability.tracing import record_decision

def decision_context(ctx: TurnContext, diagnosis: Diagnosis) -> DecisionContext:
    """What the DecisionService may see: masked facts, never the raw query log."""
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

def compute_triage(ctx: TurnContext, diagnosis: Diagnosis) -> Triage:
    """One combined decision (issue type + priority + department) per escalation. Services
    without a native `triage` fall back to three individual calls."""
    dctx = decision_context(ctx, diagnosis)
    native = getattr(ctx.decision_service, "triage", None)
    if native is None:
        return triage_via_individual_calls(ctx.decision_service, dctx)
    return native(dctx)

def decide_issue_type(
    ctx: TurnContext,
    diagnosis: Diagnosis,
    deterministic_issue_type: IssueType,
    triage: Triage | None = None,
) -> IssueType:
    """Issue type for the ticket, from the `DecisionService` (contracts §4.5).

    `deterministic_issue_type` (the `ROOT_CAUSE_ACTION` table in `known_causes.py`) is used instead
    whenever the service's answer is below `policy.yaml: decision.min_confidence`, out of
    enum, or the provider call itself failed — `LLMStructuredDecisionService` turns all
    three of those into a confidence-0.0 `Decision`, so a single confidence check covers
    every case and a provider failure can never break the turn."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    decision = (
        triage.issue_type
        if triage is not None
        else ctx.decision_service.classify_issue_type(decision_context(ctx, diagnosis))
    )
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


def decide_priority(
    ctx: TurnContext,
    diagnosis: Diagnosis,
    issue_type: IssueType,
    deterministic_priority: Priority,
    triage: Triage | None = None,
) -> Priority:
    """Priority for the ticket, from the `DecisionService` (contracts §4.5).

    `deterministic_priority` (the `ROOT_CAUSE_ACTION` table in `known_causes.py`) is used whenever the service's
    answer is below the confidence floor. Independently of confidence, the result may never
    rank below `routing.yaml: urgency_floor[issue_type]` — the service may only ever raise
    urgency for an issue type, never lower it (contracts §4.5/§4.6)."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    routing = ctx.tenant_config.routing
    decision = (
        triage.priority
        if triage is not None
        else ctx.decision_service.assess_urgency(decision_context(ctx, diagnosis))
    )
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


def decide_department(
    ctx: TurnContext, diagnosis: Diagnosis, issue_type: IssueType, triage: Triage | None = None
) -> Department:
    """Department for the ticket, from the `DecisionService` (contracts §4.5). Its result
    is used as the ticket's department whenever `policy.yaml` does not already fix one via
    `escalate_to` for the denied action (see the call site in `handle_action`) — policy's own
    `escalate_to` remains authoritative when present, since which department has *authority*
    over an action is a policy decision, never the model's (contracts §4.6), while this call
    only decides *routing* for the (rarer) case policy leaves open."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    decision = (
        triage.department
        if triage is not None
        else ctx.decision_service.choose_department(decision_context(ctx, diagnosis))
    )
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

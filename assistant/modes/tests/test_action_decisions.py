"""Unit tests for the three `DecisionService` calls `modes.action` now makes for real
(contracts §4.5) — `choose_department`, `assess_urgency`, `classify_issue_type` — and their
confidence-floor fallback to the deterministic `ROOT_CAUSE_ACTION` table.

These exercise `modes.action.triage.decide_issue_type` / `decide_priority` / `decide_department`
directly against a minimal `TurnContext`, rather than through the full orchestrator: the
helpers only touch `ctx.decision_service`, `ctx.tenant_config`, `ctx.audit_log`,
`ctx.conversation_id`/`ctx.tenant`/`ctx.history`, so the other `TurnContext` fields (gateway,
policy engine, ticket service, provider) are irrelevant here and left as `None`.
"""
from __future__ import annotations

from core_common.types import Department, Diagnosis, DiagnosisScope, IssueType, Priority
from decision.llm_structured import LLMStructuredDecisionService
from llm.scripted import ScriptedProvider, ScriptedRule
from modes.action.triage import compute_triage, decide_department, decide_issue_type, decide_priority
from modes.context import TurnContext


def _ctx(tenant_config, audit_log, provider, conversation_id: str = "conv-decisions") -> TurnContext:
    return TurnContext(
        conversation_id=conversation_id,
        tenant_config=tenant_config,
        decision_service=LLMStructuredDecisionService(provider),
        gateway=None,
        audit_log=audit_log,
        policy_engine=None,
        action_executor=None,
        ticket_service=None,
        provider=provider,
        customer_no="NS-100001",
        masked_customer_ref="NS-100001",
        history=[],
    )


def _diagnosis(root_cause: str) -> Diagnosis:
    return Diagnosis(
        root_cause=root_cause,
        scope=DiagnosisScope.CUSTOMER_SPECIFIC,
        confidence=0.9,
        evidence={"queried_sources": [], "subscription_id": 42},
        affected_customers=["NS-100001"],
    )


def _decision_log(audit_log, conversation_id: str) -> list:
    return [e for e in audit_log.timeline(conversation_id) if e.step_type == "decision_service"]


# -- combined triage ------------------------------------------------------------------------


def test_triage_feeds_all_three_helpers_with_one_call_and_one_audit_entry_each(
    tenant_config, audit_log
) -> None:
    calls: list[str] = []

    class Counting(ScriptedProvider):
        def structured(self, **kwargs):
            calls.append(kwargs["schema"].__name__)
            return super().structured(**kwargs)

    def field(v: str) -> dict:
        return {"value": v, "confidence": 0.9, "rationale": "sure"}

    provider = Counting([
        ScriptedRule(
            match=r"double_charge",
            structured={"_TriageResult": {
                "issue_type": field("double_charge"), "priority": field("HIGH"), "department": field("BILLING"),
            }},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("double_charge")

    triage = compute_triage(ctx, diagnosis)
    issue_type = decide_issue_type(ctx, diagnosis, IssueType.OTHER, triage)
    priority = decide_priority(ctx, diagnosis, issue_type, Priority.NORMAL, triage)
    department = decide_department(ctx, diagnosis, issue_type, triage)

    assert calls == ["_TriageResult"]
    assert (issue_type, priority, department) == (IssueType.DOUBLE_CHARGE, Priority.HIGH, Department.BILLING)
    summaries = [e.summary.split(" ")[0] for e in _decision_log(audit_log, ctx.conversation_id)]
    assert summaries == ["classify_issue_type", "assess_urgency", "choose_department"]


def test_triage_provider_failure_uses_deterministic_fallbacks(tenant_config, audit_log) -> None:
    ctx = _ctx(tenant_config, audit_log, ScriptedProvider([]))
    diagnosis = _diagnosis("double_charge")
    triage = compute_triage(ctx, diagnosis)
    issue_type = decide_issue_type(ctx, diagnosis, IssueType.DOUBLE_CHARGE, triage)
    assert issue_type is IssueType.DOUBLE_CHARGE
    assert decide_priority(ctx, diagnosis, issue_type, Priority.HIGH, triage) is Priority.HIGH
    assert decide_department(ctx, diagnosis, issue_type, triage) is Department.BILLING


# -- classify_issue_type --------------------------------------------------------------------


def test_classify_issue_type_confident_answer_is_used(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"double_charge",
            structured={"_IssueTypeResult": {"value": "double_charge", "confidence": 0.9, "rationale": "sure"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("double_charge")

    issue_type = decide_issue_type(ctx, diagnosis, IssueType.OTHER)

    assert issue_type is IssueType.DOUBLE_CHARGE
    entries = _decision_log(audit_log, ctx.conversation_id)
    assert entries and entries[-1].evidence["escalated"] is False


def test_classify_issue_type_low_confidence_falls_back_to_deterministic(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"double_charge",
            structured={"_IssueTypeResult": {"value": "other", "confidence": 0.1, "rationale": "unsure"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("double_charge")

    issue_type = decide_issue_type(ctx, diagnosis, IssueType.DOUBLE_CHARGE)

    assert issue_type is IssueType.DOUBLE_CHARGE  # deterministic fallback, not the model's "other"
    entries = _decision_log(audit_log, ctx.conversation_id)
    assert entries and entries[-1].evidence["escalated"] is True


def test_classify_issue_type_provider_error_falls_back_without_raising(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([])  # no fixtures at all -> every call is unscripted
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("stuck_provisioning")

    issue_type = decide_issue_type(ctx, diagnosis, IssueType.STUCK_PROVISIONING)

    assert issue_type is IssueType.STUCK_PROVISIONING


# -- assess_urgency --------------------------------------------------------------------------


def test_assess_urgency_confident_answer_is_used(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"stuck_provisioning",
            structured={"_UrgencyResult": {"value": "HIGH", "confidence": 0.9, "rationale": "sure"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("stuck_provisioning")

    priority = decide_priority(ctx, diagnosis, IssueType.STUCK_PROVISIONING, Priority.NORMAL)

    assert priority is Priority.HIGH


def test_assess_urgency_low_confidence_falls_back_to_deterministic(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("missed_installation")

    priority = decide_priority(ctx, diagnosis, IssueType.MISSED_INSTALLATION, Priority.NORMAL)

    assert priority is Priority.NORMAL
    entries = _decision_log(audit_log, ctx.conversation_id)
    assert entries and entries[-1].evidence["escalated"] is True


def test_assess_urgency_never_drops_below_routing_floor(tenant_config, audit_log) -> None:
    # routing.yaml: urgency_floor.payment_system_down -> URGENT. Even a *confident* model
    # answer must never be allowed to undercut that floor (contracts §4.5/§4.6).
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"payment_system_down",
            structured={"_UrgencyResult": {"value": "LOW", "confidence": 0.95, "rationale": "confident but wrong"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("payment_system_down")

    priority = decide_priority(ctx, diagnosis, IssueType.PAYMENT_SYSTEM_DOWN, Priority.URGENT)

    assert priority is Priority.URGENT


def test_assess_urgency_may_raise_above_the_floor(tenant_config, audit_log) -> None:
    # floor for stuck_provisioning is NORMAL; a confident HIGH answer is allowed through.
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"stuck_provisioning",
            structured={"_UrgencyResult": {"value": "URGENT", "confidence": 0.99, "rationale": "sure"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("stuck_provisioning")

    priority = decide_priority(ctx, diagnosis, IssueType.STUCK_PROVISIONING, Priority.NORMAL)

    assert priority is Priority.URGENT


# -- choose_department -----------------------------------------------------------------------


def test_choose_department_confident_answer_is_used(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"double_charge",
            structured={"_DepartmentResult": {"value": "BILLING", "confidence": 0.9, "rationale": "sure"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("double_charge")

    department = decide_department(ctx, diagnosis, IssueType.DOUBLE_CHARGE)

    assert department is Department.BILLING


def test_choose_department_low_confidence_routes_via_issue_routing_table(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([
        ScriptedRule(
            match=r"double_charge",
            structured={"_DepartmentResult": {"value": "FIELD_INSTALL", "confidence": 0.1, "rationale": "unsure"}},
        ),
    ])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("double_charge")

    department = decide_department(ctx, diagnosis, IssueType.DOUBLE_CHARGE)

    assert department is Department.BILLING  # routing.yaml: issue_routing.double_charge
    entries = _decision_log(audit_log, ctx.conversation_id)
    assert entries and entries[-1].evidence["escalated"] is True


def test_choose_department_provider_error_falls_back_without_raising(tenant_config, audit_log) -> None:
    provider = ScriptedProvider([])
    ctx = _ctx(tenant_config, audit_log, provider)
    diagnosis = _diagnosis("double_charge")

    department = decide_department(ctx, diagnosis, IssueType.DOUBLE_CHARGE)

    assert department is Department.BILLING

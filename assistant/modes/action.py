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

_SUCCESS_REPLY_TR = {
    "retry_provisioning_job": (
        "Kurulum işinizin takıldığını tespit ettim ve şimdi yeniden başlattım. Birkaç "
        "dakika içinde tamamlanmasını bekliyorum."
    ),
    "enqueue_provisioning_job": (
        "Ödemenizin alındığını ama kurulum sürecinin başlamadığını gördüm; kurulum "
        "işinizi şimdi kuyruğa aldım."
    ),
}


@dataclass
class ActionStepResult:
    reply_tr: str
    next_mode: Mode
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)


def _department_tr(ctx: TurnContext, department: Department | str | None) -> str:
    if department is None:
        return "ilgili ekip"
    code = department.value if isinstance(department, Department) else str(department)
    dept_cfg = ctx.tenant_config.departments.get(code)
    return dept_cfg.display_name_tr if dept_cfg else code


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
        "amount_try": 50.0,
    }
    return context


def _build_params(root_cause: str, diagnosis: Diagnosis, customer_no: str) -> dict[str, Any]:
    evidence = diagnosis.evidence
    base = {"customer_no": customer_no, "subscription_id": evidence.get("subscription_id")}
    if root_cause == diagnostic.ROOT_CAUSE_STUCK_PROVISIONING:
        base["job_id"] = evidence.get("job_id")
    if root_cause == diagnostic.ROOT_CAUSE_REGIONAL_OUTAGE:
        base["amount_try"] = 50.0
        base["reason"] = f"Bölgesel kesinti telafisi ({diagnosis.incident_no})"
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
_FINDING_TR: dict[str, str] = {
    "stuck_provisioning": "Kurulum (provizyon) işleminizin takılı kaldığını tespit ettim.",
    "paid_not_active": (
        "Ödemenizin alındığını, ancak aboneliğinizin aktifleştirilmediğini tespit ettim."
    ),
    "double_charge": "Aynı tutarın hesabınızdan iki kez tahsil edildiğini tespit ettim.",
    "missed_installation": "Kurulum randevunuzun gerçekleştirilmediğini tespit ettim.",
    "regional_outage": (
        "Bölgenizde devam eden bir altyapı arızası olduğunu tespit ettim; sorun size özel değil."
    ),
    "payment_system_down": (
        "Ödeme sistemimiz şu anda geçici olarak hizmet veremiyor. Sorun kartınızda ya da "
        "cihazınızda değil; sistem normale döndüğünde ödemenizi tekrar deneyebilirsiniz."
    ),
    "no_issue_found": "Kayıtlarınızda sorunun kaynağını kesin olarak tespit edemedim.",
    "customer_not_found": "Kayıtlarınıza ulaşamadım.",
}


def _finding_tr(root_cause: str) -> str:
    return _FINDING_TR.get(root_cause, "Durumunuzu inceledim.")


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
    blocked_reason_tr: str,
) -> ActionStepResult:
    evidence = diagnosis.evidence
    ticket = build_structured_ticket(
        conversation_id=ctx.conversation_id,
        department=department,
        issue_type=issue_type,
        priority=priority,
        subject_tr=f"{issue_type.value} — {customer_no}",
        body_tr=(
            f"Teşhis sonucu: {diagnosis.root_cause}. Müşteri {customer_no} için "
            f"'{attempted_action}' işlemi politika tarafından engellendi: {blocked_reason_tr} "
            "Lütfen manuel olarak değerlendirin."
        ),
        requester_customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
        suggested_next_step_tr=blocked_reason_tr,
        urgency_reason_tr=f"Teşhis güveni: {diagnosis.confidence:.2f}",
        evidence_record_ids={k: v for k, v in evidence.items() if k != "queried_sources"},
        evidence_queried_sources=evidence.get("queried_sources", []),
        attempted_steps=[
            {"step": f"diagnosis:{diagnosis.root_cause}", "result": "established", "outcome": "info"},
            {"step": f"policy_check:{attempted_action}", "result": blocked_reason_tr, "outcome": "blocked"},
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
        blocked_reason_tr,
        {"ticket_key": ticket_ref.ticket_key, "department": department.value},
        tenant=ctx.tenant,
    )
    dept_tr = _department_tr(ctx, department)
    reply_tr = (
        f"{_finding_tr(diagnosis.root_cause)} Bu işlemi doğrudan tamamlama yetkim yok, "
        f"bu nedenle talebinizi {dept_tr} ekibine ilettim. "
        f"Takip numaranız: {ticket_ref.ticket_key}."
    )
    return ActionStepResult(
        reply_tr=reply_tr,
        next_mode=Mode.ESCALATED,
        ticket_key=ticket_ref.ticket_key,
        actions=[{"label_tr": f"Bilet oluşturuldu: {department.value}", "ticket_key": ticket_ref.ticket_key}],
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
            reply_tr=(
                "Hesabınızı ve aboneliğinizi kontrol ettim, şu anda belirgin bir sorun "
                "görünmüyor. Sorun devam ederse lütfen detay paylaşın."
            ),
            next_mode=Mode.CLOSING,
        )
    if root_cause == diagnostic.ROOT_CAUSE_CUSTOMER_NOT_FOUND:
        return ActionStepResult(
            reply_tr="Müşteri numaranızı bulamadım, lütfen numaranızı kontrol edip tekrar deneyin.",
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
                prompt_tr=exc.decision.reason_tr or "Bu işlem onayınızı gerektiriyor.",
            )
            reply_tr = (
                f"Bölgenizde devam eden bir arıza kaydı bulundu ({diagnosis.incident_no}) ve "
                f"talebinizi {incident_ticket.ticket_key} numaralı kayda ekledim. Kesinti için "
                "hesabınıza 50 TL'ye kadar iyi niyet kredisi tanımlayabilirim, ancak bu geri "
                "alınamaz bir işlem olduğu için önce onayınızı almam gerekiyor. Onaylıyor musunuz?"
            )
            return ActionStepResult(
                reply_tr=reply_tr,
                next_mode=Mode.AWAITING_APPROVAL,
                ticket_key=incident_ticket.ticket_key,
                requires_approval=True,
                approval_id=approval_id,
                actions=[{"label_tr": "Politika kontrolü: apply_outage_credit"}],
            )
        except PolicyDenied as exc:
            reply_tr = (
                f"Bölgenizde devam eden bir arıza kaydı bulundu ({diagnosis.incident_no}) ve "
                f"talebinizi {incident_ticket.ticket_key} numaralı kayda ekledim. Bu kesinti "
                f"için kredi tanımlayamıyorum: {exc.decision.reason_tr}"
            )
            return ActionStepResult(
                reply_tr=reply_tr, next_mode=Mode.ESCALATED, ticket_key=incident_ticket.ticket_key,
            )
        else:
            # allowed without confirmation (shouldn't happen per policy.yaml, but handle it)
            reply_tr = (
                f"Bölgenizde devam eden bir arıza kaydı bulundu ({diagnosis.incident_no}) ve "
                f"talebinizi {incident_ticket.ticket_key} numaralı kayda ekledim."
            )
            return ActionStepResult(reply_tr=reply_tr, next_mode=Mode.CLOSING, ticket_key=incident_ticket.ticket_key)

    mapping = _ROOT_CAUSE_ACTION.get(root_cause)
    if mapping is None:
        return ActionStepResult(
            reply_tr=(
                "Durumu inceledim ancak otomatik çözebileceğim bir adım bulamadım; isterseniz "
                "bir temsilciye aktarabilirim."
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
            blocked_reason_tr=exc.decision.reason_tr or "politika engeli",
        )
    except ConfirmationRequired as exc:
        approval_id = _persist_pending_approval(
            ctx, action_name=action_name, params=params, policy_context=policy_context,
            prompt_tr=exc.decision.reason_tr or "Bu işlem onayınızı gerektiriyor.",
        )
        return ActionStepResult(
            reply_tr=f"{exc.decision.reason_tr} Onaylıyor musunuz?",
            next_mode=Mode.AWAITING_APPROVAL,
            requires_approval=True,
            approval_id=approval_id,
            actions=[{"label_tr": f"Politika kontrolü: {action_name}"}],
        )
    else:
        reply_tr = _SUCCESS_REPLY_TR.get(
            action_name, f"{action_name} işlemini başarıyla gerçekleştirdim."
        )
        return ActionStepResult(
            reply_tr=reply_tr,
            next_mode=Mode.CLOSING,
            actions=[{"label_tr": f"İşlem uygulandı: {action_name}"}],
        )


def _persist_pending_approval(
    ctx: TurnContext,
    *,
    action_name: str,
    params: dict[str, Any],
    policy_context: dict[str, Any],
    prompt_tr: str,
) -> str:
    with session_scope(ctx.audit_log.session_factory) as session:
        row = PendingApproval(
            conversation_id=ctx.conversation_id,
            action_name=action_name,
            params={"action_params": params, "policy_context": policy_context},
            prompt_tr=prompt_tr,
            status="pending",
        )
        session.add(row)
        session.flush()
        approval_id = str(row.id)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.APPROVAL_REQUESTED,
        f"approval #{approval_id} requested for action '{action_name}'",
        prompt_tr,
        {"action_name": action_name},
        tenant=ctx.tenant,
    )
    return approval_id

"""ACTION mode (contracts §4.3/§4.6): map a `Diagnosis` to a candidate action and run it
**only** through `policy.executor.ActionExecutor` — never call a mutating tool directly.

- `PolicyDenied` -> build a structured ticket for `decision.escalate_to`, mode ESCALATED.
- `ConfirmationRequired` -> persist a `pending_approvals` row, mode AWAITING_APPROVAL.
- success -> inform the customer what was fixed, mode CLOSING.

Import as: ``from modes.action import handle_action, ActionStepResult``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core_common.models import PendingApproval
from core_common.db import session_scope
from core_common.types import Department, Diagnosis, IssueType, Mode, Priority, StepType
from modes import diagnostic
from modes.context import TurnContext
from modes.incident_policy import ensure_incident_ticket
from modes.ticket_links import record_ticket_link
from policy.executor import ConfirmationRequired, PolicyDenied
from tickets.builder import build_structured_ticket

# root_cause -> (policy action name, IssueType, Priority, Department hint for a denial
# that somehow has no escalate_to in policy.yaml).
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
        f"Bu işlemi doğrudan tamamlama yetkim yok, bu nedenle talebinizi {dept_tr} "
        f"ekibine ilettim. Takip numaranız: {ticket_ref.ticket_key}."
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

    action_name, issue_type, priority = mapping
    policy_context = _build_policy_context(ctx, diagnosis)
    params = _build_params(root_cause, diagnosis, customer_no)

    try:
        ctx.action_executor.execute(action_name, params, policy_context, confirmed=False)
    except PolicyDenied as exc:
        department = exc.decision.escalate_to or Department.SUBSCRIPTION_OPS
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

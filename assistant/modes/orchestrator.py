"""The single entry point the API uses (contracts §4.3). Ties the explicit state
machine (`modes.machine`) to the mode handlers (`modes.router/advisory/diagnostic/
action/status_query/incident_policy`), persists conversation state in `asst.*`
(`core_common.models`), and writes every step to the `AuditLog`.

Import as: ``from modes.orchestrator import Orchestrator, TurnResult``.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import sessionmaker

from core_common.config import TenantConfig
from core_common.db import session_scope
from core_common.models import AlertEvent, Conversation, Message, PendingApproval, TicketLink
from core_common.settings import AssistantSettings
from core_common.types import AdvisoryProfile, Department, Diagnosis, IssueType, Mode, Priority, StepType
from decision.base import DecisionService
from llm.base import LLMProvider
from mcp_gateway.action_runner import make_action_runner
from modes import action, advisory, diagnostic, status_query
from modes.context import TurnContext
from mcp_gateway.types import ToolBudgetExceeded
from modes.machine import LimitExceeded, StateMachine
from modes.router import route_intent
from modes.ticket_links import record_ticket_link
from modes.tool_data import first_record
from observability.tracing import trace_turn
from policy.engine import PolicyEngine
from policy.executor import ActionExecutor, PolicyDenied
from privacy.masking import mask_text
from tickets.builder import build_structured_ticket
from tickets.client import TicketService

_TICKET_STATUS_EN = {
    "NEW": "new",
    "TRIAGE": "under review",
    "IN_PROGRESS": "in progress",
    "WAITING_CUSTOMER": "waiting for information from you",
    "RESOLVED": "resolved",
    "CLOSED": "closed",
    "REJECTED": "rejected",
}

_HISTORY_CAP = 16


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TurnResult(BaseModel):
    """contracts §4.2 response shape for `POST /api/chat` / `GET /api/chat/stream`."""

    model_config = ConfigDict(frozen=False)

    conversation_id: str
    mode: str
    reply_en: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    diagnosis: dict[str, Any] | None = None


@dataclass
class _StepOutcome:
    reply_en: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    diagnosis: dict[str, Any] | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        tenant_config: TenantConfig,
        settings: AssistantSettings,
        provider: LLMProvider,
        decision_service: DecisionService,
        gateway: Any,
        audit_log: Any,
        session_factory: sessionmaker,
    ) -> None:
        self.tenant_config = tenant_config
        self.settings = settings
        self.provider = provider
        self.decision_service = decision_service
        self.gateway = gateway
        self.audit_log = audit_log
        self.session_factory = session_factory

        self.policy_engine = PolicyEngine(tenant_config.policy)
        self.action_executor = ActionExecutor(
            self.policy_engine, audit_log, make_action_runner(gateway)
        )
        self.ticket_service = TicketService(gateway)
        self.machine = StateMachine(tenant_config.policy.limits)

    @property
    def tenant(self) -> str:
        return self.tenant_config.tenant_name

    # -- public API ---------------------------------------------------------------------

    def handle_message(
        self, *, conversation_id: str | None, customer_no: str | None, message: str
    ) -> TurnResult:
        conv_id = conversation_id or f"conv-{uuid.uuid4().hex[:12]}"
        self.gateway.reset_turn_budget()

        state, existing_customer_no = self._load_state(conv_id)
        customer_no = customer_no or state.get("customer_no") or existing_customer_no
        if customer_no:
            state["customer_no"] = customer_no

        masked_message = mask_text(message).masked
        mode = Mode(state.get("mode") or Mode.ROUTER.value)
        if mode in (Mode.CLOSING, Mode.ESCALATED):
            mode = Mode.ROUTER

        history: list[dict[str, str]] = list(state.get("history", []))

        ctx = TurnContext(
            conversation_id=conv_id,
            tenant_config=self.tenant_config,
            decision_service=self.decision_service,
            gateway=self.gateway,
            audit_log=self.audit_log,
            policy_engine=self.policy_engine,
            action_executor=self.action_executor,
            ticket_service=self.ticket_service,
            provider=self.provider,
            customer_no=customer_no,
            masked_customer_ref=customer_no,
            history=history,
        )

        with trace_turn(
            masked_customer_ref=customer_no or "anonymous",
            conversation_id=conv_id,
            mode=mode.value,
            metadata={"tenant": self.tenant},
        ):
            try:
                new_mode, step = self._dispatch(ctx, state, mode, masked_message, customer_no)
            except LimitExceeded as exc:
                new_mode = Mode.ESCALATED if self.machine.can_transition(mode, Mode.ESCALATED) else Mode.CLOSING
                step = _StepOutcome(
                    reply_en=(
                        "I have reached the number of steps allowed in this conversation, so "
                        "I am passing you to an agent."
                    )
                )
                self.audit_log.append(
                    conv_id, StepType.ESCALATION, f"limit exceeded: {exc}", str(exc), {}, tenant=self.tenant
                )
            except ToolBudgetExceeded as exc:
                # The per-turn tool budget is the assistant's own guard rail, not a company
                # failure: hitting it must end the turn politely and hand over, never break
                # the conversation.
                new_mode = Mode.ESCALATED if self.machine.can_transition(mode, Mode.ESCALATED) else Mode.CLOSING
                step = _StepOutcome(
                    reply_en=(
                        "I reached my lookup limit before finishing my checks. I am passing "
                        "this to an agent, who will get back to you as soon as possible."
                    )
                )
                self.audit_log.append(
                    conv_id,
                    StepType.ESCALATION,
                    "tool call budget exhausted mid-turn",
                    str(exc),
                    {"queried_sources": ["assistant:tool_budget"]},
                    tenant=self.tenant,
                )

        history.append({"role": "user", "content": masked_message})
        history.append({"role": "assistant", "content": step.reply_en})
        state["history"] = history[-_HISTORY_CAP:]
        state["mode"] = new_mode.value

        self._append_message(conv_id, "user", masked_message, mode.value)
        self._append_message(conv_id, "assistant", step.reply_en, new_mode.value)
        self._save_state(conv_id, state, customer_no)

        return TurnResult(
            conversation_id=conv_id,
            mode=new_mode.value,
            reply_en=step.reply_en,
            actions=step.actions,
            ticket_key=step.ticket_key,
            requires_approval=step.requires_approval,
            approval_id=step.approval_id,
            diagnosis=step.diagnosis,
        )

    def resolve_approval(self, *, approval_id: str, granted: bool) -> TurnResult:
        try:
            approval_pk = int(approval_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown approval_id: {approval_id!r}") from exc

        with session_scope(self.session_factory) as session:
            row = session.get(PendingApproval, approval_pk)
            if row is None or row.status != "pending":
                raise ValueError(f"unknown or already-resolved approval_id: {approval_id!r}")
            conv_id = row.conversation_id
            action_name = row.action_name
            stored = row.params or {}
            action_params = stored.get("action_params", {})
            policy_context = stored.get("policy_context", {})
            row.status = "granted" if granted else "denied"
            row.resolved_at = _utcnow()

        self.gateway.reset_turn_budget()
        self.audit_log.append(
            conv_id,
            StepType.APPROVAL_GRANTED if granted else StepType.APPROVAL_DENIED,
            f"approval #{approval_id} {'granted' if granted else 'denied'} for '{action_name}'",
            None,
            {"action_name": action_name},
            tenant=self.tenant,
        )

        state, customer_no = self._load_state(conv_id)
        current_mode = Mode(state.get("mode", Mode.AWAITING_APPROVAL.value))
        ticket_key: str | None = None

        if not granted:
            reply_en = "Understood, I have not carried out that action."
            new_mode = Mode.CLOSING
        else:
            try:
                self.action_executor.execute(action_name, action_params, policy_context, confirmed=True)
            except PolicyDenied as exc:
                ctx = self._build_ctx(conv_id, customer_no)
                name, contact = self._lookup_requester(ctx, customer_no)
                department = exc.decision.escalate_to or Department.SUBSCRIPTION_OPS
                ticket = build_structured_ticket(
                    conversation_id=conv_id,
                    department=department,
                    issue_type=IssueType.OTHER,
                    priority=Priority.NORMAL,
                    subject_en=f"Approved action could not be carried out — {action_name}",
                    body_en=f"'{action_name}' was approved but blocked by policy: {exc.decision.reason_en}",
                    requester_customer_no=customer_no or "unknown",
                    requester_name=name,
                    requester_contact=contact,
                    suggested_next_step_en=exc.decision.reason_en or "Manuel inceleme gerekiyor.",
                    urgency_reason_en="An approved action could not be completed.",
                    evidence_record_ids={"action_name": action_name},
                    evidence_queried_sources=[],
                )
                ticket_ref = self.ticket_service.create(ticket)
                record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=ticket_ref.department)
                ticket_key = ticket_ref.ticket_key
                reply_en = f"I could not complete the action, so I have passed your request to the relevant team: {ticket_key}."
                new_mode = Mode.ESCALATED
            else:
                reply_en = "Thank you for approving; I have completed the action."
                new_mode = Mode.CLOSING

        state["mode"] = new_mode.value
        self._save_state(conv_id, state, customer_no)
        self._append_message(conv_id, "assistant", reply_en, new_mode.value)

        return TurnResult(
            conversation_id=conv_id,
            mode=new_mode.value,
            reply_en=reply_en,
            actions=[],
            ticket_key=ticket_key,
            requires_approval=False,
            approval_id=approval_id,
            diagnosis=state.get("diagnosis"),
        )

    def handle_ticket_event(self, event: dict[str, Any]) -> TurnResult | None:
        ticket_key = event.get("ticket_key")
        if not ticket_key:
            return None

        with session_scope(self.session_factory) as session:
            link = (
                session.query(TicketLink)
                .filter_by(ticket_key=ticket_key)
                .order_by(TicketLink.id.desc())
                .first()
            )
            if link is None:
                return None
            conv_id = link.conversation_id
            new_status = event.get("new_status") or event.get("status")
            link.last_known_status = new_status
            link.notified_status = new_status

        status_en = _TICKET_STATUS_EN.get(new_status, new_status or "updated")
        reply_en = f"Your request ({ticket_key}) has been updated: {status_en}."
        comment = event.get("comment")
        if comment:
            reply_en += f" Not: {mask_text(str(comment)).masked}"

        self.audit_log.append(
            conv_id,
            StepType.USER_NOTIFIED,
            f"notified user of ticket {ticket_key} status change",
            None,
            {"ticket_key": ticket_key, "new_status": new_status},
            tenant=self.tenant,
        )
        self._append_message(conv_id, "assistant", reply_en, Mode.CLOSING.value)

        return TurnResult(
            conversation_id=conv_id,
            mode=Mode.CLOSING.value,
            reply_en=reply_en,
            actions=[],
            ticket_key=ticket_key,
            requires_approval=False,
            approval_id=None,
            diagnosis=None,
        )

    def handle_alert(self, alert: dict[str, Any]) -> TurnResult | None:
        fingerprint = str(alert.get("fingerprint") or alert.get("alertname") or uuid.uuid4().hex)
        alertname = str(alert.get("alertname", "unknown"))
        severity = str(alert.get("severity", "warning"))
        status = str(alert.get("status", "firing"))

        with session_scope(self.session_factory) as session:
            session.add(
                AlertEvent(
                    alert_fingerprint=fingerprint,
                    alertname=alertname,
                    severity=severity,
                    status=status,
                    labels=alert.get("labels") or {},
                    annotations=alert.get("annotations") or {},
                    handled=False,
                )
            )

        conv_id = f"alert-{fingerprint}"
        self.gateway.reset_turn_budget()
        self.audit_log.append(
            conv_id,
            StepType.ALERT_RECEIVED,
            f"received alert '{alertname}' (status={status})",
            None,
            {"alertname": alertname, "severity": severity},
            tenant=self.tenant,
        )

        if status != "firing" or alertname not in ("PaymentGatewayDown", "CoreApiDown"):
            self._mark_alert_handled(fingerprint, "no automated action for this alert")
            return None

        department_code = str(alert.get("department") or Department.TECHNICAL_INFRA.value)
        try:
            department = Department(department_code)
        except ValueError:
            department = Department.TECHNICAL_INFRA

        ticket = build_structured_ticket(
            conversation_id=conv_id,
            department=department,
            issue_type=IssueType.PAYMENT_SYSTEM_DOWN,
            priority=Priority.URGENT,
            subject_en=f"{alertname} — izleme sisteminden otomatik tespit",
            body_en=(
                f"Alert from the monitoring system: {alert.get('summary') or alertname}. "
                f"Status: {status}, severity: {severity}."
            ),
            requester_customer_no="SYSTEM",
            requester_name="Monitoring System",
            requester_contact="n/a",
            suggested_next_step_en="Check the payment/core API infrastructure.",
            urgency_reason_en="Risk of a system-wide outage.",
            evidence_record_ids={"alert_fingerprint": fingerprint},
            evidence_queried_sources=["mcp-monitoring:webhooks/alertmanager"],
            source="monitoring",
        )
        ticket_ref = self.ticket_service.create(ticket)
        record_ticket_link(
            self._build_ctx(conv_id), ticket_key=ticket_ref.ticket_key, department=ticket_ref.department
        )

        channel = self.tenant_config.departments.get(department.value)
        if channel is not None:
            self.gateway.call_sync(
                "post_department_message",
                {
                    "channel": channel.channel,
                    "title": alertname,
                    "text": f"{alertname} fired, ticket: {ticket_ref.ticket_key}",
                    "severity": severity,
                    "source": "assistant",
                },
            )

        self.audit_log.append(
            conv_id,
            StepType.TICKET_CREATED,
            f"created ticket {ticket_ref.ticket_key} from alert '{alertname}'",
            None,
            {"ticket_key": ticket_ref.ticket_key},
            tenant=self.tenant,
        )
        self._mark_alert_handled(fingerprint, f"ticket {ticket_ref.ticket_key} created")

        reply_en = (
            "The payment system is temporarily unavailable. The technical team has been "
            f"notified; reference number: {ticket_ref.ticket_key}."
        )
        return TurnResult(
            conversation_id=conv_id,
            mode=Mode.ESCALATED.value,
            reply_en=reply_en,
            actions=[],
            ticket_key=ticket_ref.ticket_key,
            requires_approval=False,
            approval_id=None,
            diagnosis=None,
        )

    # -- dispatch loop --------------------------------------------------------------------

    def _dispatch(
        self, ctx: TurnContext, state: dict[str, Any], mode: Mode, masked_message: str, customer_no: str | None
    ) -> tuple[Mode, _StepOutcome]:
        diagnosis_obj: Diagnosis | None = None
        hops = 0

        while True:
            hops += 1
            if hops > 5:
                return Mode.CLOSING, _StepOutcome(
                    reply_en="I am passing this conversation to an agent."
                )

            if mode is Mode.ROUTER:
                router_result = route_intent(ctx, masked_message)
                mode = self.machine.transition(Mode.ROUTER, router_result.next_mode)
                if router_result.reply_en is not None:
                    return mode, _StepOutcome(reply_en=router_result.reply_en)
                if mode is Mode.ADVISORY:
                    # Keep what the customer has already told us about their household.
                    # Re-entering advisory after a recommendation (CLOSING -> ROUTER ->
                    # ADVISORY) means they are refining, not starting over, and asking
                    # "what do you use the internet for?" again reads as amnesia.
                    # Only the per-round bookkeeping resets.
                    previous_profile = state.get("advisory_profile") or {}
                    state["advisory_profile"] = previous_profile
                    state["advisory_awaiting_field"] = None
                    state["advisory_questions_asked"] = 0
                    state["_advisory_first_turn"] = not previous_profile
                continue

            if mode is Mode.ADVISORY:
                profile = AdvisoryProfile.model_validate(state.get("advisory_profile") or {})
                is_first = bool(state.pop("_advisory_first_turn", False))
                awaiting_field = state.get("advisory_awaiting_field")
                questions_asked = state.get("advisory_questions_asked", 0)
                step = advisory.handle_turn(
                    ctx,
                    profile=profile,
                    awaiting_field=awaiting_field,
                    questions_asked=questions_asked,
                    masked_message=None if is_first else masked_message,
                    is_first_turn=is_first,
                )
                state["advisory_profile"] = step.profile.model_dump(mode="json")
                state["advisory_awaiting_field"] = step.awaiting_field
                state["advisory_questions_asked"] = step.questions_asked
                target = Mode.CLOSING if step.done else Mode.ADVISORY
                new_mode = self.machine.transition(Mode.ADVISORY, target)
                return new_mode, _StepOutcome(reply_en=step.reply_en)

            if mode is Mode.DIAGNOSTIC:
                if not customer_no:
                    return Mode.CLOSING, _StepOutcome(
                        reply_en="Please sign in (with your customer number) first so that I can diagnose the problem."
                    )
                diagnosis_obj = diagnostic.run_diagnosis(ctx, customer_no)
                state["diagnosis"] = diagnosis_obj.model_dump(mode="json")
                mode = self.machine.transition(Mode.DIAGNOSTIC, Mode.ACTION)
                continue

            if mode is Mode.STATUS_QUERY:
                if not customer_no:
                    return Mode.CLOSING, _StepOutcome(
                        reply_en="Please sign in first so that I can check your status."
                    )
                sq_step = status_query.handle_status_query(ctx, customer_no)
                new_mode = self.machine.transition(Mode.STATUS_QUERY, sq_step.next_mode)
                return new_mode, _StepOutcome(reply_en=sq_step.reply_en)

            if mode is Mode.ACTION:
                if diagnosis_obj is None:
                    diagnosis_obj = Diagnosis.model_validate(state.get("diagnosis") or {})
                name, contact = self._lookup_requester(ctx, customer_no)
                action_step = action.handle_action(
                    ctx,
                    diagnosis=diagnosis_obj,
                    customer_no=customer_no or "unknown",
                    requester_name=name,
                    requester_contact=contact,
                )
                new_mode = self.machine.transition(Mode.ACTION, action_step.next_mode)
                return new_mode, _StepOutcome(
                    reply_en=action_step.reply_en,
                    actions=action_step.actions,
                    ticket_key=action_step.ticket_key,
                    requires_approval=action_step.requires_approval,
                    approval_id=action_step.approval_id,
                    diagnosis=diagnosis_obj.model_dump(mode="json"),
                )

            if mode is Mode.AWAITING_APPROVAL:
                return mode, _StepOutcome(
                    reply_en="Please use one of the options on the approval card."
                )

            # ESCALATED/CLOSING reached mid-loop (shouldn't normally happen): stop here.
            return mode, _StepOutcome(
                reply_en="This conversation is complete. Feel free to write about something new."
            )

    # -- helpers --------------------------------------------------------------------------

    def _lookup_requester(self, ctx: TurnContext, customer_no: str | None) -> tuple[str, str]:
        if not customer_no:
            return "Customer", "unknown"
        outcome = ctx.call_tool_cached("find_customer", {"customer_no": customer_no})
        data = first_record(outcome)
        if data is not None:
            name = data.get("full_name") or "Customer"
            contact = data.get("phone") or data.get("email") or "unknown"
            return str(name), str(contact)
        return "Customer", "unknown"

    def _mark_alert_handled(self, fingerprint: str, note: str) -> None:
        with session_scope(self.session_factory) as session:
            row = (
                session.query(AlertEvent)
                .filter_by(alert_fingerprint=fingerprint)
                .order_by(AlertEvent.id.desc())
                .first()
            )
            if row is not None:
                row.handled = True
                row.handling_note = note

    def _load_state(self, conversation_id: str) -> tuple[dict[str, Any], str | None]:
        with session_scope(self.session_factory) as session:
            conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
            if conv is None:
                conv = Conversation(
                    conversation_id=conversation_id,
                    tenant=self.tenant,
                    mode=Mode.ROUTER.value,
                    state={},
                )
                session.add(conv)
                session.flush()
            return dict(conv.state or {}), conv.customer_no

    def _save_state(self, conversation_id: str, state: dict[str, Any], customer_no: str | None) -> None:
        with session_scope(self.session_factory) as session:
            conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
            if conv is None:
                conv = Conversation(conversation_id=conversation_id, tenant=self.tenant)
                session.add(conv)
            conv.state = state
            conv.mode = state.get("mode")
            if customer_no:
                conv.customer_no = customer_no
                conv.masked_customer_ref = customer_no

    def _append_message(self, conversation_id: str, role: str, content: str, mode: str) -> None:
        with session_scope(self.session_factory) as session:
            session.add(
                Message(
                    conversation_id=conversation_id, role=role, content=content, mode=mode, masked=True
                )
            )

    def _build_ctx(self, conversation_id: str, customer_no: str | None = None) -> TurnContext:
        return TurnContext(
            conversation_id=conversation_id,
            tenant_config=self.tenant_config,
            decision_service=self.decision_service,
            gateway=self.gateway,
            audit_log=self.audit_log,
            policy_engine=self.policy_engine,
            action_executor=self.action_executor,
            ticket_service=self.ticket_service,
            provider=self.provider,
            customer_no=customer_no,
            masked_customer_ref=customer_no,
        )

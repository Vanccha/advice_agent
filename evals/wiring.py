"""Builds the real `Orchestrator`, wired exactly the way `assistant/api/main.py` and
`assistant/modes/tests/conftest.py` do it — copied wiring, not reinvented: a `ScriptedProvider`
(no API key) wrapped in the same `MaskingProvider` production uses, the default
`LLMStructuredDecisionService`, a live `ToolGateway.from_tenant_config(...)` (never
`FakeGateway` — the scenario suite must exercise the real company stack), and the
assistant's own Postgres-backed audit/session plumbing.

Import as: ``from evals.wiring import EvalHarness``.
"""
from __future__ import annotations

import uuid
from typing import Any

from audit.log import AuditLog
from core_common.config import TenantConfig, load_tenant_config
from core_common.db import bootstrap_schema, get_engine, get_sessionmaker, session_scope
from core_common.models import ActionRecord, TicketLink
from core_common.settings import AssistantSettings, get_settings
from decision.factory import get_decision_service
from llm.base import LLMProvider
from llm.factory import get_provider
from llm.guard import MaskingProvider
from llm.scripted import ScriptedProvider, ScriptedRule
from mcp_gateway.gateway import ToolGateway
from modes.orchestrator import Orchestrator, TurnResult
from tickets.client import TicketService


class EvalHarness:
    """One instance per `evals.run` invocation. Holds every collaborator the suites need
    and the small set of read helpers (ticket/action-record lookups) that go straight to
    the assistant's own Postgres state rather than re-deriving it from a `TurnResult`."""

    def __init__(self, rules: list[ScriptedRule]) -> None:
        self.settings: AssistantSettings = get_settings()

        engine = get_engine()
        bootstrap_schema(engine)
        self.session_factory = get_sessionmaker()
        self.audit_log = AuditLog(self.session_factory)

        self.tenant_config: TenantConfig = load_tenant_config()

        self.provider: LLMProvider
        if self.settings.EVAL_MODE == "live":
            # Exists so the code path is real, but is not exercised by this harness: no
            # API key is configured in this environment (contracts §8 `EVAL_MODE=live`).
            self.provider = get_provider(self.settings)
        else:
            self.provider = MaskingProvider(ScriptedProvider(rules, record=True))

        self.decision_service = get_decision_service(self.provider, self.settings, self.tenant_config)

        self.gateway = ToolGateway.from_tenant_config(
            self.tenant_config, self.audit_log, conversation_id="eval-init", actor="eval"
        )
        self.ticket_service = TicketService(self.gateway)

        self.orchestrator = Orchestrator(
            tenant_config=self.tenant_config,
            settings=self.settings,
            provider=self.provider,
            decision_service=self.decision_service,
            gateway=self.gateway,
            audit_log=self.audit_log,
            session_factory=self.session_factory,
        )

    # -- driving a conversation ---------------------------------------------------------

    def new_conversation_id(self, prefix: str) -> str:
        return f"eval-{prefix}-{uuid.uuid4().hex[:8]}"

    def send(self, conversation_id: str, customer_no: str | None, message: str) -> TurnResult:
        """One chat turn through the real orchestrator. Tags the shared gateway's audit
        entries with this conversation id first (the gateway instance is reused across
        every case in the run to avoid re-discovering the MCP tool catalogue each time)."""
        self.gateway.conversation_id = conversation_id
        return self.orchestrator.handle_message(
            conversation_id=conversation_id, customer_no=customer_no, message=message
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Direct tool call, bypassing the orchestrator — used for eval-only bookkeeping
        (e.g. looking up which customers a regional incident affected). Resets the shared
        gateway's per-turn call budget first: this is a harness-side lookup, not part of
        the conversation turn that last ran, so it must never be refused merely because
        that turn (or an earlier lookup) already spent the shared gateway's budget."""
        self.gateway.reset_turn_budget()
        return self.gateway.call_sync(tool_name, arguments)

    def resolve_approval(self, approval_id: str, granted: bool) -> TurnResult:
        """Resolve a pending approval (e.g. the regional-outage goodwill credit offer) so a
        conversation that paused at `AWAITING_APPROVAL` reaches its true terminal mode.
        Never grants by default (callers pass `granted=False` to avoid any real monetary
        side effect against the live payment system, unless they explicitly want otherwise)."""
        self.gateway.reset_turn_budget()
        return self.orchestrator.resolve_approval(approval_id=approval_id, granted=granted)

    # -- assistant-state lookups (contracts §1.5) ----------------------------------------

    def ticket_links_for(self, conversation_id: str) -> list[TicketLink]:
        with session_scope(self.session_factory) as session:
            rows = (
                session.query(TicketLink)
                .filter_by(conversation_id=conversation_id)
                .order_by(TicketLink.id.asc())
                .all()
            )
            # Detach plain data (the session closes on return) — callers only read fields.
            return [
                TicketLink(
                    id=r.id,
                    conversation_id=r.conversation_id,
                    ticket_key=r.ticket_key,
                    department=r.department,
                    last_known_status=r.last_known_status,
                    notified_status=r.notified_status,
                )
                for r in rows
            ]

    def action_records_for(self, conversation_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            rows = (
                session.query(ActionRecord)
                .filter_by(conversation_id=conversation_id)
                .order_by(ActionRecord.id.asc())
                .all()
            )
            return [
                {
                    "action_name": r.action_name,
                    "executed": r.executed,
                    "policy_allowed": r.policy_allowed,
                    "policy_reason": r.policy_reason,
                }
                for r in rows
            ]

    def get_ticket(self, ticket_key: str) -> dict[str, Any]:
        self.gateway.reset_turn_budget()
        return self.ticket_service.get(ticket_key)

    def audit_timeline(self, conversation_id: str) -> list[Any]:
        """Every hash-chained audit row for one conversation (contracts §1.5/§4.8) — used
        to report, per case, whether a `DecisionService` call took the confident path or
        the deterministic-table fallback (`StepType.DECISION_SERVICE` entries carry
        `evidence.escalated`)."""
        return self.audit_log.timeline(conversation_id)

    def tickets_for_incident(self, incident_no: str) -> list[dict[str, Any]]:
        self.gateway.reset_turn_budget()
        return self.ticket_service.find_for_incident(incident_no)

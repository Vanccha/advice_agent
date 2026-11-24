"""The single entry point the API uses: owns the turn lifecycle and wires collaborators.

Where this sits: called by `api/main.py` (and `evals/`). Delegates mode routing to
`modes.dispatch`, persistence to `modes.conversation_store`, approvals to
`modes.approvals` and webhooks to `modes.events`; every step is written to the `AuditLog`.

Key ideas:
- Facade: four public methods hide the whole state machine from the HTTP layer.
- Turn lifecycle: load state -> mask the message -> dispatch -> save state -> reply.
  Everything customer-visible happens in the middle; the edges are bookkeeping.
- Guard rails end a turn politely (hand over to an agent), never with an error page.
- Per-turn collaborators: each turn gets its own gateway view and call budget so one
  conversation can never spend another's tool calls.

Read next: modes/dispatch.py, modes/state.py, modes/machine.py.

Import as: ``from modes.orchestrator import Orchestrator, TurnResult``.
See: docs/contracts.md §4.3.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import sessionmaker

from core_common.config import TenantConfig
from core_common.settings import AssistantSettings
from core_common.types import Mode, StepType
from decision.base import DecisionService
from llm.base import LLMProvider
from mcp_gateway.action_runner import make_action_runner
from mcp_gateway.types import ToolBudgetExceeded
from modes import approvals, events, replies
from modes.context import TurnContext
from modes.conversation_store import ConversationStore
from modes.dispatch import dispatch_turn
from modes.machine import LimitExceeded, StateMachine
from modes.results import StepOutcome, TurnResult
from modes.state import HISTORY_CAP
from observability.tracing import trace_turn
from policy.engine import PolicyEngine
from policy.executor import ActionExecutor
from privacy.masking import mask_text
from tickets.client import TicketService

__all__ = ["Orchestrator", "TurnResult"]


class Orchestrator:
    """Public facade of the conversation engine (one instance per tenant)."""

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
        # The shared "root" gateway owns adapter config + tool catalogue. It is never used
        # for a turn's calls directly; every turn gets its own view via `_scope()`.
        self.gateway = gateway
        self.audit_log = audit_log
        self.session_factory = session_factory

        self.policy_engine = PolicyEngine(tenant_config.policy)
        self.machine = StateMachine(tenant_config.policy.limits)
        self.store = ConversationStore(session_factory, tenant_config.tenant_name)

    @property
    def tenant(self) -> str:
        return self.tenant_config.tenant_name

    # --- Public API -----------------------------------------------------------------
    def handle_message(
        self, *, conversation_id: str | None, customer_no: str | None, message: str
    ) -> TurnResult:
        """Answer one customer message (creating the conversation if it is new)."""
        conv_id = conversation_id or f"conv-{uuid.uuid4().hex[:12]}"

        state, stored_customer_no = self.store.load(conv_id)
        customer_no = customer_no or state.customer_no or stored_customer_no
        if customer_no:
            state.customer_no = customer_no

        # Masking happens first: nothing downstream (model, trace, audit, storage) ever
        # sees the raw text.
        masked_message = mask_text(message).masked
        mode = state.mode
        if mode in (Mode.CLOSING, Mode.ESCALATED):
            # A finished conversation starts fresh on the next message.
            mode = Mode.ROUTER

        ctx = self.build_ctx(conv_id, customer_no)
        # The turn's view of the history already includes the message being answered
        # (decision calls must see it). `state.history` is the persisted list and gets the
        # user/assistant pair appended exactly once below.
        ctx.history = [*state.history, {"role": "user", "content": masked_message}]

        with trace_turn(
            masked_customer_ref=customer_no or "anonymous",
            conversation_id=conv_id,
            mode=mode.value,
            metadata={"tenant": self.tenant},
        ):
            try:
                new_mode, step = dispatch_turn(
                    ctx, self.machine, state, mode, masked_message, customer_no
                )
            except LimitExceeded as exc:
                new_mode = self._handover_mode(mode)
                step = StepOutcome(reply_en=replies.STEP_LIMIT_REACHED)
                self.audit_log.append(
                    conv_id, StepType.ESCALATION, f"limit exceeded: {exc}", str(exc), {}, tenant=self.tenant
                )
            except ToolBudgetExceeded as exc:
                # The per-turn tool budget is the assistant's own guard rail, not a company
                # failure: hitting it must end the turn politely and hand over.
                new_mode = self._handover_mode(mode)
                step = StepOutcome(reply_en=replies.TOOL_BUDGET_REACHED)
                self.audit_log.append(
                    conv_id,
                    StepType.ESCALATION,
                    "tool call budget exhausted mid-turn",
                    str(exc),
                    {"queried_sources": ["assistant:tool_budget"]},
                    tenant=self.tenant,
                )

        state.history = [
            *state.history,
            {"role": "user", "content": masked_message},
            {"role": "assistant", "content": step.reply_en},
        ][-HISTORY_CAP:]
        state.mode = new_mode

        self.store.append_message(conv_id, "user", masked_message, mode.value)
        self.store.append_message(conv_id, "assistant", step.reply_en, new_mode.value)
        self.store.save(conv_id, state, customer_no)

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
        """The customer approved or declined a pending irreversible action."""
        return approvals.resolve_approval(self, approval_id=approval_id, granted=granted)

    def handle_ticket_event(self, event: dict[str, Any]) -> TurnResult | None:
        """A department changed a ticket's status; tell the customer."""
        return events.handle_ticket_event(self, event)

    def handle_alert(self, alert: dict[str, Any]) -> TurnResult | None:
        """A monitoring alert fired; open a ticket for infrastructure outages."""
        return events.handle_alert(self, alert)

    # --- Collaborator wiring --------------------------------------------------------
    def build_ctx(self, conversation_id: str, customer_no: str | None = None) -> TurnContext:
        """Everything one turn's handlers need, built fresh so budgets never leak across turns."""
        gateway, executor, ticket_service = self._scope(conversation_id)
        return TurnContext(
            conversation_id=conversation_id,
            tenant_config=self.tenant_config,
            decision_service=self.decision_service,
            gateway=gateway,
            audit_log=self.audit_log,
            policy_engine=self.policy_engine,
            action_executor=executor,
            ticket_service=ticket_service,
            provider=self.provider,
            customer_no=customer_no,
            masked_customer_ref=customer_no,
        )

    def _scope(self, conversation_id: str) -> tuple[Any, ActionExecutor, TicketService]:
        """A gateway view bound to this conversation with its own call budget, plus the
        executor and ticket service wired to that same view."""
        for_turn = getattr(self.gateway, "for_turn", None)
        gateway = for_turn(conversation_id) if for_turn is not None else self.gateway
        gateway.reset_turn_budget()
        executor = ActionExecutor(
            self.policy_engine, self.audit_log, make_action_runner(gateway),
            conversation_action_guard=self.machine.check_actions_per_conversation,
        )
        return gateway, executor, TicketService(gateway)

    def _handover_mode(self, mode: Mode) -> Mode:
        """ESCALATED when the machine allows it from `mode`, otherwise simply CLOSING."""
        return Mode.ESCALATED if self.machine.can_transition(mode, Mode.ESCALATED) else Mode.CLOSING

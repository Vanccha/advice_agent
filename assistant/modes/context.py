"""Shared per-turn context handed to every mode module (contracts §4.3).

Not a database model — a plain bag of already-constructed collaborators plus this turn's
masked inputs, so `router.py`/`advisory.py`/`diagnostic.py`/`action.py`/`status_query.py`
never need to know how the orchestrator builds a gateway, policy engine or ticket service.

Import as: ``from modes.context import TurnContext``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from core_common.config import TenantConfig
from decision.base import DecisionService
from policy.engine import PolicyEngine
from policy.executor import ActionExecutor
from tickets.client import TicketService


class _Gateway(Protocol):
    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...
    def reset_turn_budget(self) -> None: ...


@dataclass
class TurnContext:
    """Everything one turn's mode handler needs. Mutable counters (``tool_calls_used``,
    ``questions_asked``, ``actions_used``) are shared across the whole turn (and, for
    ``actions_used``, the whole conversation) so ``modes.machine.StateMachine`` can
    enforce ``policy.yaml: limits``."""

    conversation_id: str
    tenant_config: TenantConfig
    decision_service: DecisionService
    gateway: _Gateway
    audit_log: Any
    policy_engine: PolicyEngine
    action_executor: ActionExecutor
    ticket_service: TicketService

    customer_no: str | None
    masked_customer_ref: str | None

    # Masked conversation turns, oldest first: [{"role": "user"|"assistant", "content": str}]
    history: list[dict[str, str]] = field(default_factory=list)

    tool_calls_used: int = 0

    @property
    def tenant(self) -> str:
        return self.tenant_config.tenant_name

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool through the gateway and bump this turn's call counter."""
        self.tool_calls_used += 1
        return self.gateway.call_sync(tool_name, arguments)

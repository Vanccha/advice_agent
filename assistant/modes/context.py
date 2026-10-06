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
from llm.base import LLMProvider
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
    # contracts §4.3 advisory verbalization: the same (already PII-guarded) provider the
    # rest of the turn uses, so ADVISORY can ask it to narrate `recommend_packages`'
    # output in the tenant's language without any mode needing to know how the provider is constructed.
    provider: LLMProvider

    customer_no: str | None
    masked_customer_ref: str | None

    # Masked conversation turns, oldest first: [{"role": "user"|"assistant", "content": str}]
    history: list[dict[str, str]] = field(default_factory=list)

    tool_calls_used: int = 0
    _tool_cache: dict[tuple[str, tuple[tuple[str, Any], ...]], Any] = field(default_factory=dict)

    @property
    def tenant(self) -> str:
        return self.tenant_config.tenant_name

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool through the gateway and bump this turn's call counter."""
        self.tool_calls_used += 1
        return self.gateway.call_sync(tool_name, arguments)

    def call_tool_cached(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Like `call_tool`, but memoised per (tool_name, arguments) for the lifetime of
        this turn — read-only lookups (``find_customer`` and the like) are frequently
        needed by more than one mode handler in the same turn, and re-fetching them would
        needlessly spend the turn's ``max_tool_calls_per_turn`` budget."""
        key = (tool_name, tuple(sorted(arguments.items())))
        if key not in self._tool_cache:
            self._tool_cache[key] = self.call_tool(tool_name, arguments)
        return self._tool_cache[key]

"""Per-turn context: the bag of collaborators and counters every mode handler receives.

Where this sits: built fresh for each turn by `Orchestrator.build_ctx`, passed to
`modes.dispatch` and from there to every handler. Not a database model.

Key ideas:
- Dependency injection by parameter object: handlers never construct a gateway, policy
  engine or ticket service, which keeps them unit-testable with fakes.
- Per-turn budgets: `tool_calls_used` and the tool cache live here so one turn cannot
  spend more than ``max_tool_calls_per_turn`` and repeated lookups cost one call.

Read next: modes/orchestrator.py (where it is built), modes/dispatch.py.

Import as: ``from modes.context import TurnContext``.
See: docs/contracts.md §4.3.
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
    # The same (already PII-guarded) provider the rest of the turn uses, so ADVISORY can
    # have `recommend_packages` output narrated without knowing how a provider is built.
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

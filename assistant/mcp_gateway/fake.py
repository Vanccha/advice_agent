"""``FakeGateway``: the tool gateway's surface backed by canned answers, no network.

Where this sits: ``mcp_gateway`` package. ``modes`` tests and ``evals/`` run against it instead
of ``gateway.ToolGateway``; no containers and no MCP SDK are involved.

Key ideas:
- Same interface, different backend: code under test cannot tell the fake from the real thing
  (``for_turn``, ``call``, ``call_sync``, ``catalog``), so tests exercise the real code paths.
- Faithful failure: an unknown tool behaves like a dead adapter (``ok=False``,
  ``ADAPTER_UNAVAILABLE``), and the per-turn budget is enforced the same way.
- ``calls_made`` records every call, so tests can assert what the assistant asked for.

Read next: ``mcp_gateway/gateway.py``.
See: docs/contracts.md §3.
"""
from __future__ import annotations

import asyncio
from typing import Any

from mcp_gateway.tool_audit import audit_tool_call
from mcp_gateway.types import ToolBudgetExceeded, ToolCallOutcome, ToolSpec

CannedResponse = ToolCallOutcome | dict[str, Any]


class FakeGateway:
    """Canned stand-in for `ToolGateway`.

    Construct with ``responses={tool_name: canned_value, ...}`` where `canned_value` is a
    ready-made `ToolCallOutcome` or a plain dict (wrapped as `ok=True, data=<dict>`).
    `adapter_map` says which adapter each tool "belongs to" (default `"fake"`), purely for
    `catalog()` and audit bookkeeping. A tool absent from `responses` behaves like a dead
    adapter: `ok=False, error_code="ADAPTER_UNAVAILABLE"`, never an exception.
    """

    def __init__(
        self,
        responses: dict[str, CannedResponse] | None = None,
        audit_log: Any = None,
        *,
        adapter_map: dict[str, str] | None = None,
        conversation_id: str = "fake-conversation",
        tenant: str = "netswift",
        actor: str = "assistant",
        max_calls_per_turn: int = 8,
    ) -> None:
        self._responses = dict(responses or {})
        self._adapter_map = dict(adapter_map or {})
        self._audit_log = audit_log
        self.conversation_id = conversation_id
        self.tenant = tenant
        self.actor = actor
        self.max_calls_per_turn = max_calls_per_turn
        self._calls_this_turn = 0
        self.calls_made: list[tuple[str, dict[str, Any]]] = []

    def for_turn(self, conversation_id: str, *, actor: str | None = None) -> "FakeGateway":
        """Per-turn view (mirrors `ToolGateway.for_turn`): shares the canned responses and
        the `calls_made` record with this instance, but has its own conversation id and
        call budget."""
        view = FakeGateway(
            audit_log=self._audit_log,
            conversation_id=conversation_id,
            tenant=self.tenant,
            actor=actor or self.actor,
            max_calls_per_turn=self.max_calls_per_turn,
        )
        view._responses = self._responses
        view._adapter_map = self._adapter_map
        view.calls_made = self.calls_made
        return view

    def reset_turn_budget(self) -> None:
        self._calls_this_turn = 0

    def set_response(self, tool_name: str, response: CannedResponse, *, adapter: str = "fake") -> None:
        self._responses[tool_name] = response
        self._adapter_map[tool_name] = adapter

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=name,
                description=f"fake tool '{name}'",
                adapter=self._adapter_map.get(name, "fake"),
            )
            for name in self._responses
        ]

    async def catalog(self) -> dict[str, list[ToolSpec]]:
        grouped: dict[str, list[ToolSpec]] = {}
        for spec in await self.list_tools():
            grouped.setdefault(spec.adapter, []).append(spec)
        return grouped

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        if self._calls_this_turn >= self.max_calls_per_turn:
            raise ToolBudgetExceeded(tool_name, self.max_calls_per_turn)
        self._calls_this_turn += 1
        self.calls_made.append((tool_name, arguments))

        canned = self._responses.get(tool_name)
        if canned is None:
            outcome = ToolCallOutcome(
                ok=False,
                error_code="ADAPTER_UNAVAILABLE",
                error_message=f"FakeGateway has no canned response for tool '{tool_name}'",
                adapter=self._adapter_map.get(tool_name),
            )
        elif isinstance(canned, ToolCallOutcome):
            outcome = canned
        else:
            outcome = ToolCallOutcome(
                ok=True, data=canned, adapter=self._adapter_map.get(tool_name, "fake")
            )

        self._audit(tool_name, arguments, outcome)
        return outcome

    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        return asyncio.run(self.call(tool_name, arguments))

    def _audit(self, tool_name: str, arguments: dict[str, Any], outcome: ToolCallOutcome) -> None:
        if self._audit_log is None:
            return
        audit_tool_call(
            self._audit_log,
            conversation_id=self.conversation_id,
            tenant=self.tenant,
            actor=self.actor,
            tool_name=tool_name,
            arguments=arguments,
            outcome=outcome,
        )

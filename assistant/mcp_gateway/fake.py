"""`FakeGateway`: the same public surface as `gateway.ToolGateway`, backed by a dict of
canned tool responses instead of live adapters — no containers, no network, no `mcp` SDK
involved. The modes layer and `evals/` run against this.

Import as: ``from mcp_gateway.fake import FakeGateway``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from audit.log import digest_tool_output
from core_common.types import StepType
from mcp_gateway.types import ToolBudgetExceeded, ToolCallOutcome, ToolSpec
from privacy.masking import mask_payload

CannedResponse = ToolCallOutcome | dict[str, Any]


class FakeGateway:
    """Construct with ``responses={tool_name: canned_value, ...}`` where `canned_value` is
    either a ready-made `ToolCallOutcome` or a plain dict (wrapped as `ok=True,
    data=<dict>`). `adapter_map` optionally says which adapter each tool "belongs to"
    (defaults to `"fake"`), purely for `catalog()`/audit bookkeeping — it changes nothing
    about how a call resolves. A tool name absent from `responses` behaves exactly like a
    dead adapter in the real gateway: `ok=False, error_code="ADAPTER_UNAVAILABLE"`, never
    an exception.
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
        masked_arguments = mask_payload(arguments)
        digest_source = outcome.data if outcome.ok else {"error_code": outcome.error_code}
        digest = digest_tool_output(digest_source)
        summary = f"called tool '{tool_name}'" if outcome.ok else f"called tool '{tool_name}' -> {outcome.error_code}"
        self._audit_log.append(
            self.conversation_id,
            StepType.TOOL_CALL,
            summary,
            outcome.error_message if not outcome.ok else None,
            {"adapter": outcome.adapter, "duration_ms": outcome.duration_ms},
            tenant=self.tenant,
            actor=self.actor,
            tool_name=tool_name,
            tool_input=masked_arguments,
            tool_output_digest=digest,
        )

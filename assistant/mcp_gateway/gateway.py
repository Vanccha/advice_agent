"""The live tool gateway: the only way the assistant reaches the company's systems.

Where this sits: ``mcp_gateway`` package. ``modes`` (and ``policy.executor`` through
``action_runner``) call ``ToolGateway.call``; it talks MCP over streamable HTTP to the adapters
in ``integrations/`` and records each call with ``tool_audit``. ``fake.FakeGateway`` has the
same surface for tests.

Key ideas:
- Per-turn scoping: a gateway (or its ``for_turn`` view) has a call budget of
  ``policy.yaml: limits.max_tool_calls_per_turn``; call N+1 raises ``ToolBudgetExceeded`` so a runaway model loop
  cannot hammer company systems.
- Graceful degradation: a dead or absent adapter yields ``ok=False,
  error_code="ADAPTER_UNAVAILABLE"`` instead of raising, so the assistant still runs when
  some adapters are down.
- Config, not hostnames: adapter URLs come from the tenant's ``adapters`` config (hard rule 4).
- Discovery by catalogue: tools are discovered from the adapters (``list_tools``) and routed
  by name; the catalogue is shared between turn views.

Read next: ``mcp_gateway/fake.py``, ``mcp_gateway/tool_audit.py``, ``mcp_gateway/types.py``.
See: docs/contracts.md §3.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from core_common.config_models import AdapterConfig, TenantConfig

# The third-party MCP client SDK. This package is deliberately NOT named `mcp`, so this
# import resolves to the installed SDK instead of shadowing it. A missing SDK degrades to
# "no tools discovered" rather than breaking import.
try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    MCP_SDK_AVAILABLE = True
except Exception as _sdk_exc:  # pragma: no cover - only when the SDK is absent
    ClientSession = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    MCP_SDK_AVAILABLE = False
    MCP_SDK_IMPORT_ERROR = _sdk_exc
from mcp_gateway.tool_audit import audit_tool_call
from mcp_gateway.types import ToolBudgetExceeded, ToolCallOutcome, ToolSpec

# contracts §3: each adapter's ToolResult `source` tag.
_ADAPTER_SOURCE = {
    "core": "core_api",
    "payment": "payment_api",
    "ticketing": "ticketing_api",
    "monitoring": "monitoring_api",
    "notification": "notification_api",
}


def _content_to_text(content: Any) -> str:
    """Flatten an MCP `CallToolResult.content` list of content blocks into plain text."""
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


class ToolGateway:
    """Calls company tools through MCP adapters, with a per-turn budget and an audit trail.

    Args:
        adapters: adapter key -> `AdapterConfig` (URL from tenant config).
        audit_log: receives one entry per call.
        max_calls_per_turn: the budget; reset with `reset_turn_budget()` or use `for_turn()`.
    """

    def __init__(
        self,
        adapters: dict[str, AdapterConfig],
        audit_log: Any,
        *,
        conversation_id: str,
        tenant: str,
        actor: str = "assistant",
        max_calls_per_turn: int = 8,
    ) -> None:
        self._adapters = adapters
        self._audit_log = audit_log
        self.conversation_id = conversation_id
        self.tenant = tenant
        self.actor = actor
        self.max_calls_per_turn = max_calls_per_turn
        self._calls_this_turn = 0
        self._tool_index: dict[str, str] | None = None  # tool_name -> adapter key
        self._tool_specs: dict[str, list[ToolSpec]] = {}
        # Turn views share the root gateway's tool catalogue, so discovery runs once.
        self._catalog_owner: ToolGateway = self

    def for_turn(self, conversation_id: str, *, actor: str | None = None) -> "ToolGateway":
        """A per-turn view: same adapters, audit log and (shared) tool catalog, but its own
        `conversation_id`, `actor` and call budget. Cheap; build one per turn/request."""
        view = ToolGateway(
            self._adapters,
            self._audit_log,
            conversation_id=conversation_id,
            tenant=self.tenant,
            actor=actor or self.actor,
            max_calls_per_turn=self.max_calls_per_turn,
        )
        view._catalog_owner = self._catalog_owner
        return view

    @classmethod
    def from_tenant_config(
        cls,
        tenant_config: TenantConfig,
        audit_log: Any,
        *,
        conversation_id: str,
        actor: str = "assistant",
        max_calls_per_turn: int | None = None,
    ) -> "ToolGateway":
        """Build a gateway from a tenant's adapters; the budget defaults to the policy limit."""
        limit = (
            max_calls_per_turn
            if max_calls_per_turn is not None
            else tenant_config.policy.limits.max_tool_calls_per_turn
        )
        return cls(
            dict(tenant_config.adapters),
            audit_log,
            conversation_id=conversation_id,
            tenant=tenant_config.tenant_name,
            actor=actor,
            max_calls_per_turn=limit,
        )

    def reset_turn_budget(self) -> None:
        """Start a new turn's call budget on a reused instance."""
        self._calls_this_turn = 0

    # --- Discovery ----------------------------------------------------------

    async def _discover_adapter(self, adapter_key: str, adapter_cfg: AdapterConfig) -> list[ToolSpec]:
        """List one adapter's tools; an unreachable adapter simply contributes none."""
        if not MCP_SDK_AVAILABLE:
            return []
        try:
            async with streamable_http_client(adapter_cfg.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
        except Exception:
            return []  # adapter down: degrade gracefully, the rest of the turn still works
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description or "",
                adapter=adapter_key,
                input_schema=tool.input_schema or {},
            )
            for tool in result.tools
        ]

    async def _ensure_catalog(self) -> None:
        """Discover tools on first use (turn views delegate to the root gateway)."""
        owner = self._catalog_owner
        if owner is not self:
            await owner._ensure_catalog()
            self._tool_index, self._tool_specs = owner._tool_index, owner._tool_specs
            return
        if self._tool_index is not None:
            return
        await self.refresh_catalog()

    async def refresh_catalog(self) -> None:
        """Re-run discovery against every configured adapter. Safe to call repeatedly
        (e.g. after an adapter that was down comes back up mid-conversation)."""
        if self._catalog_owner is not self:
            await self._catalog_owner.refresh_catalog()
            await self._ensure_catalog()
            return
        index: dict[str, str] = {}
        specs_by_adapter: dict[str, list[ToolSpec]] = {}
        adapter_items = list(self._adapters.items())
        results = await asyncio.gather(
            *(self._discover_adapter(key, cfg) for key, cfg in adapter_items)
        )
        for (key, _cfg), specs in zip(adapter_items, results):
            specs_by_adapter[key] = specs
            for spec in specs:
                index[spec.name] = key
        self._tool_index = index
        self._tool_specs = specs_by_adapter

    async def list_tools(self) -> list[ToolSpec]:
        """Every tool every reachable adapter advertises."""
        await self._ensure_catalog()
        return [spec for specs in self._tool_specs.values() for spec in specs]

    async def catalog(self) -> dict[str, list[ToolSpec]]:
        """Tools grouped by adapter key, so the modes layer can ask e.g. "is monitoring
        available?" via ``"monitoring" in (await gateway.catalog())``."""
        await self._ensure_catalog()
        return dict(self._tool_specs)

    # --- Calling ------------------------------------------------------------

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        """Call one tool, route it to its adapter and audit the result.

        Raises `ToolBudgetExceeded` once the turn's budget is spent. Every other failure
        (no adapter advertises the tool, connection error, tool error) is returned as an
        `ok=False` outcome.
        """
        if self._calls_this_turn >= self.max_calls_per_turn:
            raise ToolBudgetExceeded(tool_name, self.max_calls_per_turn)
        self._calls_this_turn += 1

        await self._ensure_catalog()
        adapter_key = self._tool_index.get(tool_name) if self._tool_index else None

        if adapter_key is None or adapter_key not in self._adapters:
            outcome = ToolCallOutcome(
                ok=False,
                error_code="ADAPTER_UNAVAILABLE",
                error_message=f"no reachable adapter advertises tool '{tool_name}'",
                adapter=adapter_key,
            )
            self._audit(tool_name, arguments, outcome)
            return outcome

        adapter_cfg = self._adapters[adapter_key]
        start = time.monotonic()
        try:
            async with streamable_http_client(adapter_cfg.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            outcome = ToolCallOutcome(
                ok=False,
                error_code="ADAPTER_UNAVAILABLE",
                error_message=str(exc),
                adapter=adapter_key,
                duration_ms=duration_ms,
            )
            self._audit(tool_name, arguments, outcome)
            return outcome

        duration_ms = (time.monotonic() - start) * 1000
        data = result.structured_content if result.structured_content is not None else _content_to_text(result.content)
        if result.is_error:
            outcome = ToolCallOutcome(
                ok=False,
                data=data,
                error_code="TOOL_ERROR",
                error_message=_content_to_text(result.content),
                source=_ADAPTER_SOURCE.get(adapter_key),
                adapter=adapter_key,
                duration_ms=duration_ms,
            )
        else:
            outcome = ToolCallOutcome(
                ok=True,
                data=data,
                source=_ADAPTER_SOURCE.get(adapter_key),
                adapter=adapter_key,
                duration_ms=duration_ms,
            )
        self._audit(tool_name, arguments, outcome)
        return outcome

    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        """Sync convenience wrapper for non-async callers. Must not be called from
        inside a running event loop (use `await gateway.call(...)` there instead)."""
        return asyncio.run(self.call(tool_name, arguments))

    # --- Audit --------------------------------------------------------------

    def _audit(self, tool_name: str, arguments: dict[str, Any], outcome: ToolCallOutcome) -> None:
        audit_tool_call(
            self._audit_log,
            conversation_id=self.conversation_id,
            tenant=self.tenant,
            actor=self.actor,
            tool_name=tool_name,
            arguments=arguments,
            outcome=outcome,
        )

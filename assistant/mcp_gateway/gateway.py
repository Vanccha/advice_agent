"""The only way the product touches the company (contracts §3). `ToolGateway` is built
from one tenant's `adapters` config (never a hardcoded hostname — contracts §4 hard rule
4), connects over streamable HTTP via the `mcp` client SDK, and degrades gracefully: a
dead/absent adapter yields `ok=False, error_code="ADAPTER_UNAVAILABLE"` rather than raising,
so the modes layer can run even before every adapter exists (`mcp-ticketing`,
`mcp-monitoring`, `mcp-notification` are built later than `mcp-core`/`mcp-payment`).

A `ToolGateway` instance is scoped to one turn (its `max_calls_per_turn` budget resets via
`reset_turn_budget()` — construct a fresh gateway per turn, or call that explicitly at the
start of each new turn on a reused instance).

Import as: ``from mcp_gateway.gateway import ToolGateway``.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from audit.log import digest_tool_output
from core_common.config import AdapterConfig, TenantConfig
from core_common.types import StepType
# The third-party MCP client SDK. This package is deliberately NOT named `mcp` so that a
# plain import resolves to the installed SDK instead of shadowing it.
try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    MCP_SDK_AVAILABLE = True
except Exception as _sdk_exc:  # pragma: no cover - only when the SDK is absent
    ClientSession = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    MCP_SDK_AVAILABLE = False
    MCP_SDK_IMPORT_ERROR = _sdk_exc
from mcp_gateway.types import ToolBudgetExceeded, ToolCallOutcome, ToolSpec
from privacy.masking import mask_payload

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
        self._calls_this_turn = 0

    # -- discovery --------------------------------------------------------------------

    async def _discover_adapter(self, adapter_key: str, adapter_cfg: AdapterConfig) -> list[ToolSpec]:
        if not MCP_SDK_AVAILABLE:
            return []
        try:
            async with streamable_http_client(adapter_cfg.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
        except Exception:
            return []  # adapter down/unreachable — degrade gracefully, not an error here
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
        if self._tool_index is not None:
            return
        await self.refresh_catalog()

    async def refresh_catalog(self) -> None:
        """Re-run discovery against every configured adapter. Safe to call repeatedly
        (e.g. after an adapter that was down comes back up mid-conversation)."""
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
        await self._ensure_catalog()
        return [spec for specs in self._tool_specs.values() for spec in specs]

    async def catalog(self) -> dict[str, list[ToolSpec]]:
        """Tools grouped by adapter key, so the modes layer can ask e.g. "is monitoring
        available?" via ``"monitoring" in (await gateway.catalog())``."""
        await self._ensure_catalog()
        return dict(self._tool_specs)

    # -- calling ------------------------------------------------------------------------

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
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

    # -- audit ----------------------------------------------------------------------------

    def _audit(self, tool_name: str, arguments: dict[str, Any], outcome: ToolCallOutcome) -> None:
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

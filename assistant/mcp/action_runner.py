"""`make_action_runner(gateway)`: wires `policy.executor.ActionExecutor`'s
`action_runner` callback to a tool gateway, so neither the modes layer nor the API layer
needs to know which MCP tool backs which policy action name (contracts §4.6).

Import as: ``from mcp.action_runner import make_action_runner, ACTION_TOOL_MAP,
UnmappedActionError``.
"""
from __future__ import annotations

from typing import Any, Protocol

from mcp.types import ToolCallOutcome


class _CallableGateway(Protocol):
    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome: ...


# Policy action name (`config/tenants/*/policy.yaml` `actions.*`) -> MCP tool name
# (contracts §3). Only actions whose policy entry has `allowed: true` need an entry: a
# denied action never reaches `ActionExecutor`'s `action_runner` at all — `PolicyEngine
# .check()` stops it first (contracts §4.6), so e.g. `issue_refund`/`change_package`/
# `reschedule_installation`/`repair_infrastructure`/`cancel_subscription` are correctly
# absent here.
ACTION_TOOL_MAP: dict[str, str] = {
    "retry_provisioning_job": "retry_provisioning_job",
    "resend_activation_notification": "resend_activation_notification",
    "apply_outage_credit": "apply_outage_credit",
    "send_department_message": "post_department_message",
    # NOTE — reported gap, not fixed here (outside this agent's owned directories):
    # `config/tenants/nethiz/policy.yaml` also allows `enqueue_provisioning_job` (chaos
    # scenario b, `paid_not_active`), but contracts §3's mcp-core tool list has no tool to
    # create/enqueue a *new* provisioning job — only `retry_provisioning_job`, which
    # retries an existing one. Calling this action raises `UnmappedActionError` until
    # either a tool is added to mcp-core or this mapping is told to reuse an existing one.
}


class UnmappedActionError(RuntimeError):
    """A policy-allowed action has no known MCP tool to run it (a contract/config gap,
    not a runtime/network failure — see the `ACTION_TOOL_MAP` note above)."""

    def __init__(self, action_name: str) -> None:
        super().__init__(
            f"no MCP tool mapping for policy action '{action_name}' — see "
            "mcp.action_runner.ACTION_TOOL_MAP"
        )
        self.action_name = action_name


def make_action_runner(gateway: _CallableGateway):
    """Returns a sync `ActionRunner` (`policy.executor.ActionRunner =
    Callable[[str, dict], dict]`) ready to pass to
    `ActionExecutor(policy_engine, audit_log, action_runner=make_action_runner(gateway))`.

    Never raises for an unreachable adapter — the gateway's own
    `ok=False, error_code="ADAPTER_UNAVAILABLE"` outcome is returned (as a plain dict) like
    any other result; the caller decides whether that means "treat as failed, escalate".
    Raises `UnmappedActionError` only when `action_name` has no entry in `ACTION_TOOL_MAP`
    at all (a configuration gap `PolicyEngine` already allowed through).
    """

    def _run(action_name: str, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = ACTION_TOOL_MAP.get(action_name)
        if tool_name is None:
            raise UnmappedActionError(action_name)
        outcome = gateway.call_sync(tool_name, params)
        return outcome.model_dump(mode="json")

    return _run

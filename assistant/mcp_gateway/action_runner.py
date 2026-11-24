"""Connect the policy executor to the tool gateway: policy action name -> MCP tool.

Where this sits: ``mcp_gateway`` package. ``make_action_runner(gateway)`` produces the
``action_runner`` callback that ``policy.executor.ActionExecutor`` runs after policy allows an
action, so neither ``modes`` nor ``api`` need to know which MCP tool backs which action.

Key ideas:
- Policy decides, this only maps: by the time the runner is called, ``PolicyEngine`` has
  already allowed the action.
- Explicit allow-list mapping: an action with no entry raises ``UnmappedActionError`` instead
  of guessing a tool name.
- Graceful degradation: an unreachable adapter comes back as an ``ok=False`` result dict.

Read next: ``policy/executor.py``, ``mcp_gateway/gateway.py``.
See: docs/contracts.md §3, §4.6.
"""
from __future__ import annotations

from typing import Any, Protocol

from mcp_gateway.types import ToolCallOutcome


class _CallableGateway(Protocol):
    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome: ...


# Policy action name (`config/tenants/*/policy.yaml` `actions.*`) -> MCP tool name
# (contracts §3). Only actions with `allowed: true` need an entry: a denied action never
# reaches the runner because `PolicyEngine.check()` stops it first (contracts §4.6), so
# e.g. `issue_refund` or `cancel_subscription` are correctly absent.
ACTION_TOOL_MAP: dict[str, str] = {
    "retry_provisioning_job": "retry_provisioning_job",
    "enqueue_provisioning_job": "enqueue_provisioning_job",
    "resend_activation_notification": "resend_activation_notification",
    "apply_outage_credit": "apply_outage_credit",
    "send_department_message": "post_department_message",
}


class UnmappedActionError(RuntimeError):
    """A policy-allowed action has no MCP tool to run it.

    A configuration gap (policy allows something nobody wired up), not a network failure.
    """

    def __init__(self, action_name: str) -> None:
        super().__init__(
            f"no MCP tool mapping for policy action '{action_name}' — see "
            "mcp_gateway.action_runner.ACTION_TOOL_MAP"
        )
        self.action_name = action_name


def make_action_runner(gateway: _CallableGateway):
    """Return a sync `ActionRunner` to pass to `ActionExecutor(..., action_runner=...)`.

    Never raises for an unreachable adapter: the gateway's `ok=False,
    error_code="ADAPTER_UNAVAILABLE"` outcome comes back as a plain dict like any other
    result, and the caller decides whether to escalate. Raises `UnmappedActionError` only
    when `action_name` has no entry in `ACTION_TOOL_MAP`.
    """

    def _run(action_name: str, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = ACTION_TOOL_MAP.get(action_name)
        if tool_name is None:
            raise UnmappedActionError(action_name)
        outcome = gateway.call_sync(tool_name, params)
        return outcome.model_dump(mode="json")

    return _run

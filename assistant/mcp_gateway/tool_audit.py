"""Audit one tool call: the single place that turns a call into an audit-log entry.

Where this sits: ``mcp_gateway`` package. Both ``gateway.ToolGateway`` and ``fake.FakeGateway``
call ``audit_tool_call`` after every tool call, so live and test runs write identical entries.

Key ideas:
- Mask before writing: arguments go through ``mask_payload`` first (the audit log refuses raw PII).
- Digest, not payload: only a hash of the tool output is stored (``digest_tool_output``), so the
  trail proves what came back without keeping customer data.

Read next: ``audit/log.py``, ``mcp_gateway/gateway.py``.
See: docs/contracts.md §4.8.
"""
from __future__ import annotations

from typing import Any

from audit.log import digest_tool_output
from core_common.types import StepType
from mcp_gateway.types import ToolCallOutcome
from privacy.masking import mask_payload


def audit_tool_call(
    audit_log: Any,
    *,
    conversation_id: str,
    tenant: str,
    actor: str,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: ToolCallOutcome,
) -> None:
    """Append one ``TOOL_CALL`` entry describing `outcome` to `audit_log`."""
    digest_source = outcome.data if outcome.ok else {"error_code": outcome.error_code}
    summary = (
        f"called tool '{tool_name}'"
        if outcome.ok
        else f"called tool '{tool_name}' -> {outcome.error_code}"
    )
    audit_log.append(
        conversation_id,
        StepType.TOOL_CALL,
        summary,
        outcome.error_message if not outcome.ok else None,
        {"adapter": outcome.adapter, "duration_ms": outcome.duration_ms},
        tenant=tenant,
        actor=actor,
        tool_name=tool_name,
        tool_input=mask_payload(arguments),
        tool_output_digest=digest_tool_output(digest_source),
    )

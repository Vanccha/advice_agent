"""What the investigation model may touch: the read-only tool allowlist, scoping and audit.

Where this sits: `modes.investigation`, lowest layer. `loop.py` offers these tools to the
model and runs the calls it asks for; `prompts.py` and `finding.py` reuse the small helpers
at the bottom.

Key ideas:
- Least privilege: the model sees only the gateway catalogue intersected with
  ``routing.yaml: investigation_tools``. A tool outside it does not exist for the model.
- Server-side scoping: any ``customer_no`` argument is overwritten with the signed-in
  customer's, so a prompt-injected request cannot read somebody else's records.
- Tool output is masked (UK GDPR, contracts §4.7) and size-capped before the model sees it.

Read next: modes/investigation/loop.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core_common.types import StepType
from llm.base import ToolDefinition
from modes.context import TurnContext
from privacy.masking import mask_payload, mask_text

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 3000

# --- Tool catalogue -----------------------------------------------------------------
def available_tools(ctx: TurnContext) -> dict[str, ToolDefinition]:
    """Gateway catalogue ∩ tenant read-only allowlist, as provider-neutral definitions."""
    allowlist = set(ctx.tenant_config.routing.investigation_tools)
    if not allowlist:
        return {}
    catalog = getattr(ctx.gateway, "catalog", None)
    if catalog is None:
        return {}
    try:
        grouped = asyncio.run(catalog())
    except Exception:  # noqa: BLE001 - no catalogue means no tools, never a failed turn
        logger.warning("investigation: could not read the tool catalogue", exc_info=True)
        return {}
    tools: dict[str, ToolDefinition] = {}
    for specs in grouped.values():
        for spec in specs:
            if spec.name in allowlist and spec.name not in tools:
                tools[spec.name] = ToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema or {"type": "object", "properties": {}},
                )
    return tools


def tool_result_text(ctx: TurnContext, outcome: Any) -> str:
    """The tool result as it is shown to the model: masked first (UK GDPR, contracts §4.7),
    then size-capped. `llm.guard` re-checks the final text as a backstop."""
    if getattr(outcome, "ok", False):
        payload: Any = {"ok": True, "data": outcome.data}
    else:
        payload = {
            "ok": False,
            "error_code": getattr(outcome, "error_code", None),
            "error_message": getattr(outcome, "error_message", None),
        }
    text = json_text(mask_payload(payload, allow_unmasked=allow_unmasked(ctx)))
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "...[truncated]"
    return text


def scope_arguments(
    ctx: TurnContext, tool: ToolDefinition, arguments: dict[str, Any], customer_no: str
) -> dict[str, Any]:
    """Force any ``customer_no`` argument to the signed-in customer."""
    args = dict(arguments)
    properties = (tool.input_schema or {}).get("properties") or {}
    if "customer_no" in properties or "customer_no" in args:
        args["customer_no"] = customer_no  # the model may not look at anybody else
    return args

# --- Small shared helpers -----------------------------------------------------------
def json_text(value: Any) -> str:
    """Compact JSON for model-facing text; unknown types fall back to ``str``."""
    return json.dumps(value, default=str, ensure_ascii=False)


def allow_unmasked(ctx: TurnContext) -> frozenset[str] | None:
    """Fields the tenant lets through PII masking (None means mask everything)."""
    allowed = ctx.tenant_config.policy.pii.allow_unmasked
    return frozenset(allowed) if allowed else None


def audit(ctx: TurnContext, summary: str, reason: str | None, evidence: dict[str, Any], step: StepType) -> None:
    """Append one masked investigation step to the audit log."""
    ctx.audit_log.append(
        ctx.conversation_id,
        step,
        mask_text(summary).masked,
        mask_text(reason).masked if reason else None,
        evidence,
        tenant=ctx.tenant,
    )

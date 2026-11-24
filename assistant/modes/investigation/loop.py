"""The bounded model-and-tool loop that tries to conclude an investigation.

Where this sits: `modes.investigation`. `plan_turn` (package `__init__`) calls
`run_investigation`; this module drives the LLM provider step by step.

Key ideas:
- Bounded: `limits.max_investigation_steps` tool-using steps, at most four tool calls per
  step, then ONE final step offering only `submit_finding` so the model must conclude.
- Never raises for provider or tool trouble: failures end in an `InvestigationResult` with
  `finding=None` and a `fallback_reason`, and the caller hands over to a human.
- Refusals are data: a tool outside the allowlist is refused, audited and reported back to
  the model instead of being run.

Read next: modes/investigation/finding.py, modes/investigation/__init__.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from core_common.types import Diagnosis, StepType
from llm.base import AgentMessage, ToolCall, ToolDefinition
from mcp_gateway.types import ToolBudgetExceeded
from modes.context import TurnContext
from modes.investigation.finding import (
    SUBMIT_FINDING,
    InvestigationFinding,
    finalize_finding,
    parse_text_finding,
    submit_tool,
)
from modes.investigation.prompts import build_system
from modes.investigation.tools import (
    audit,
    available_tools,
    json_text,
    scope_arguments,
    tool_result_text,
)
from observability.tracing import record_decision

logger = logging.getLogger(__name__)

_MAX_TOOL_CALLS_PER_STEP = 4


@dataclass
class InvestigationResult:
    """`finding is None` means the agentic path could not run or conclude; the caller falls
    back to asking the customer for more detail."""

    finding: InvestigationFinding | None
    steps_used: int = 0
    tools_called: list[str] = field(default_factory=list)
    refused_tools: list[str] = field(default_factory=list)
    fallback_reason: str | None = None



def _execute_tool_call(
    ctx: TurnContext,
    call: ToolCall,
    tools: dict[str, ToolDefinition],
    customer_no: str,
    result: InvestigationResult,
) -> str:
    """Run one model-requested call through the gateway, or refuse it. Returns the text
    fed back to the model. Raises nothing: budget exhaustion and failures become results."""
    tool = tools.get(call.name)
    if tool is None:
        result.refused_tools.append(call.name)
        audit(
            ctx,
            f"investigation: refused tool '{call.name}'",
            "not in the read-only investigation allowlist or not offered by any adapter",
            {"tool_name": call.name, "queried_sources": ["assistant:investigation_allowlist"]},
            StepType.MODE_DECISION,
        )
        return json_text({"ok": False, "error": f"tool '{call.name}' is not available to you"})

    args = scope_arguments(ctx, tool, call.arguments, customer_no)
    try:
        try:
            outcome = ctx.call_tool_cached(call.name, args)
        except TypeError:  # unhashable argument values cannot be memoised
            outcome = ctx.call_tool(call.name, args)
    except ToolBudgetExceeded as exc:
        audit(
            ctx,
            "investigation: tool budget exhausted",
            str(exc),
            {"tool_name": call.name, "queried_sources": ["assistant:tool_budget"]},
            StepType.MODE_DECISION,
        )
        result.fallback_reason = "tool_budget"
        return json_text(
            {"ok": False, "error": "tool call budget for this turn is spent; submit your finding now"}
        )
    except Exception as exc:  # noqa: BLE001 - a failing lookup is data for the model
        logger.warning("investigation tool %s failed", call.name, exc_info=True)
        return json_text({"ok": False, "error": f"lookup failed: {type(exc).__name__}"})
    result.tools_called.append(call.name)
    return tool_result_text(ctx, outcome)



# --- The loop -----------------------------------------------------------------------
def run_investigation(
    ctx: TurnContext,
    *,
    customer_no: str,
    diagnosis: Diagnosis,
    allow_clarification: bool = True,
    prior: dict[str, Any] | None = None,
) -> InvestigationResult:
    """Bounded model/tool loop. Never raises for provider or tool trouble: those end in
    ``InvestigationResult(finding=None, fallback_reason=...)``."""
    result = InvestigationResult(finding=None)
    max_steps = ctx.tenant_config.policy.limits.max_investigation_steps
    tools = available_tools(ctx)
    system = build_system(ctx, customer_no, diagnosis, max_steps, prior, allow_clarification)
    submit = submit_tool()

    messages: list[AgentMessage] = [
        AgentMessage(role=h["role"], content=h["content"])  # type: ignore[arg-type]
        for h in ctx.history
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]
    if not messages or messages[-1].role != "user":
        result.fallback_reason = "no_user_message"
        return result

    # `max_steps` model steps with the read-only tools, then ONE final step restricted to
    # `submit_finding` so a model that keeps looking things up still has to conclude.
    total_steps = max_steps + 1
    for step in range(1, total_steps + 1):
        final_step = step == total_steps or result.fallback_reason == "tool_budget"
        offered = [submit] if final_step else [*tools.values(), submit]
        try:
            model_step = ctx.provider.run_tool_step(
                system=system, messages=messages, tools=offered, temperature=0.0, max_tokens=1200
            )
        except Exception as exc:  # noqa: BLE001 - PIILeakError, ProviderError, unscripted...
            reason = f"provider_error:{type(exc).__name__}"
            result.fallback_reason = reason
            audit(
                ctx,
                "investigation: model step failed; falling back",
                f"{type(exc).__name__}",
                {"step": step, "provider": ctx.provider.name},
                StepType.MODE_DECISION,
            )
            return result
        result.steps_used = step
        names = [c.name for c in model_step.tool_calls]
        audit(
            ctx,
            f"investigation step {step}: model requested {names or 'no tools'}",
            None,
            {"step": step, "tool_calls": names, "model": ctx.provider.model, "final_step": final_step},
            StepType.DECISION_SERVICE,
        )
        record_decision(
            f"investigation_step_{step}",
            value=names,
            confidence=0.0,  # n/a for an intermediate step; the finding carries the confidence
            rationale="tool-use step",
            model=ctx.provider.model,
        )

        if not model_step.tool_calls:
            parsed = parse_text_finding(model_step.text)
            if parsed is not None:
                result.finding = finalize_finding(ctx, parsed, allow_clarification=allow_clarification)
                return result
            messages.append(AgentMessage(role="assistant", content=model_step.text or ""))
            messages.append(
                AgentMessage(
                    role="user",
                    content=f"Please conclude by calling the `{SUBMIT_FINDING}` tool.",
                )
            )
            continue

        messages.append(
            AgentMessage(role="assistant", content=model_step.text or "", tool_calls=model_step.tool_calls)
        )
        for index, call in enumerate(model_step.tool_calls):
            if call.name == SUBMIT_FINDING:
                try:
                    finding = InvestigationFinding.model_validate(call.arguments)
                except ValidationError as exc:
                    content = json_text({"ok": False, "error": f"invalid finding: {exc.error_count()} problem(s); fix and resubmit"})
                else:
                    result.finding = finalize_finding(
                        ctx, finding, allow_clarification=allow_clarification
                    )
                    return result
            elif final_step:
                content = json_text({"ok": False, "error": f"only {SUBMIT_FINDING} is available now"})
            elif index >= _MAX_TOOL_CALLS_PER_STEP:
                content = json_text({"ok": False, "error": "too many tool calls in one step"})
            else:
                content = _execute_tool_call(ctx, call, tools, customer_no, result)
            messages.append(
                AgentMessage(role="tool", content=content, tool_call_id=call.id, tool_name=call.name)
            )

    result.fallback_reason = result.fallback_reason or "no_finding_within_step_limit"
    audit(
        ctx,
        "investigation: no finding within the step limit; falling back",
        result.fallback_reason,
        {"steps": result.steps_used, "tools_called": result.tools_called},
        StepType.MODE_DECISION,
    )
    return result

"""Mutating `mcp-core` tools.

Where this sits: registered by `main.py`; every call goes to core-api's REST
API with the `partner-integration` key, never SQL.

Key ideas:
- Authority belongs to the company: `request_refund` and
  `reschedule_installation` call endpoints the partner account has no scope
  for (`billing:refund`, `appointments:write`) and always come back
  `ToolResult.fail("SCOPE_DENIED", ...)`. That is the boundary this layer
  exists to prove, not a bug.
- Idempotency keys: `apply_outage_credit` sends a key (caller-supplied or
  generated) so a retried call cannot credit the customer twice.
- Handlers translate response bodies into output models explicitly, so the
  company can add fields without changing the tool contract.

Read next: `models.py` for the schemas.
"""
from __future__ import annotations

import uuid
from typing import Any

from common.result import ToolResult, ok
from common.tool_spec import register_tool

from .deps import core_api_client
from .models import (
    ApplyOutageCreditInput,
    ApplyOutageCreditOutput,
    EnqueueProvisioningJobInput,
    EnqueueProvisioningJobOutput,
    RequestRefundInput,
    RequestRefundOutput,
    RescheduleInstallationInput,
    RescheduleInstallationOutput,
    ResendActivationNotificationInput,
    ResendActivationNotificationOutput,
    RetryProvisioningJobInput,
    RetryProvisioningJobOutput,
)

SOURCE_CORE = "core_api"


async def handle_retry_provisioning_job(inp: RetryProvisioningJobInput) -> ToolResult[Any]:
    """Re-queue a failed or stuck provisioning job through core-api."""
    result = await core_api_client().post(f"/v1/provisioning-jobs/{inp.job_id}/retry", SOURCE_CORE)
    if not result.ok:
        return result
    body = result.data or {}
    return ok(
        RetryProvisioningJobOutput(
            job_id=body["id"],
            subscription_id=body["subscription_id"],
            status=body["status"],
            attempt_count=body["attempt_count"],
            max_attempts=body["max_attempts"],
        ),
        SOURCE_CORE,
    )


async def handle_enqueue_provisioning_job(inp: EnqueueProvisioningJobInput) -> ToolResult[Any]:
    """Queue a first provisioning job for a subscription through core-api."""
    result = await core_api_client().post(
        f"/v1/subscriptions/{inp.subscription_id}/provisioning-jobs", SOURCE_CORE
    )
    if not result.ok:
        return result
    body = result.data or {}
    return ok(
        EnqueueProvisioningJobOutput(
            job_id=body["id"],
            subscription_id=body["subscription_id"],
            status=body["status"],
            attempt_count=body["attempt_count"],
            max_attempts=body["max_attempts"],
        ),
        SOURCE_CORE,
    )


async def handle_resend_activation_notification(inp: ResendActivationNotificationInput) -> ToolResult[Any]:
    """Resend a customer notification; core-api enforces the resend cooldown."""
    payload = {
        "customer_no": inp.customer_no,
        "template_code": inp.template_code,
        "channel": inp.channel,
    }
    result = await core_api_client().post("/v1/notifications/resend", SOURCE_CORE, json=payload)
    if not result.ok:
        return result
    body = result.data or {}
    return ok(
        ResendActivationNotificationOutput(
            notification_id=body["id"],
            customer_no=body["customer_no"],
            channel=body["channel"],
            template_code=body["template_code"],
            status=body["status"],
            sent_at=body.get("sent_at"),
        ),
        SOURCE_CORE,
    )


async def handle_apply_outage_credit(inp: ApplyOutageCreditInput) -> ToolResult[Any]:
    """Grant a goodwill credit; sends an idempotency key so a retry cannot pay twice."""
    idempotency_key = inp.idempotency_key or f"mcp-core-credit-{uuid.uuid4()}"
    payload = {
        "subscription_id": inp.subscription_id,
        "amount_gbp": inp.amount_gbp,
        "reason": inp.reason,
        "idempotency_key": idempotency_key,
    }
    result = await core_api_client().post("/v1/credits", SOURCE_CORE, json=payload)
    if not result.ok:
        return result
    body = result.data or {}
    return ok(
        ApplyOutageCreditOutput(
            credit_id=body["id"],
            subscription_id=body["subscription_id"],
            amount_gbp=body["amount_gbp"],
            reason=body["reason"],
            created_by=body["created_by"],
            idempotency_key=body["idempotency_key"],
            created_at=body["created_at"],
        ),
        SOURCE_CORE,
    )


async def handle_request_refund(inp: RequestRefundInput) -> ToolResult[Any]:
    """Always expected to fail with `SCOPE_DENIED`: `partner-integration` has no
    `billing:refund` scope (docs/contracts.md §2.1). This tool exists so the
    assistant can attempt a refund and get a clean, typed denial back instead
    of the model inventing one — the policy boundary is enforced by the
    company API itself, not by trusting the caller.
    """
    payload = {"payment_id": inp.payment_id, "amount_gbp": inp.amount_gbp, "reason": inp.reason}
    result = await core_api_client().post("/v1/refunds", SOURCE_CORE, json=payload)
    if not result.ok:
        return result
    body = result.data or {}
    return ok(
        RequestRefundOutput(
            refund_ref=body["refund_ref"],
            payment_id=inp.payment_id,
            amount_gbp=body["amount_gbp"],
            status=body["status"],
        ),
        SOURCE_CORE,
    )


async def handle_reschedule_installation(inp: RescheduleInstallationInput) -> ToolResult[Any]:
    """`partner-integration` has `appointments:read` but not `appointments:write`
    (docs/contracts.md §2.1), so this also always comes back `SCOPE_DENIED` —
    consistent with `policy.yaml`'s `reschedule_installation: allowed: false,
    escalate_to: FIELD_INSTALL` at the assistant layer: field crews own
    rescheduling, not the partner integration.
    """
    payload = {"scheduled_date": inp.scheduled_date, "time_slot": inp.time_slot}
    result = await core_api_client().post(
        f"/v1/installation-appointments/{inp.appointment_id}/reschedule", SOURCE_CORE, json=payload
    )
    if not result.ok:
        return result
    body = result.data or {}
    return ok(
        RescheduleInstallationOutput(
            appointment_id=body["id"],
            scheduled_date=body["scheduled_date"],
            time_slot=body["time_slot"],
            status=body["status"],
        ),
        SOURCE_CORE,
    )


def register_write_tools(mcp: Any) -> None:
    """Register the mutating tools (names and descriptions are part of the public contract)."""
    register_tool(
        mcp,
        name="retry_provisioning_job",
        description="Retry a stuck or failed provisioning job.",
        input_model=RetryProvisioningJobInput,
        output_model=RetryProvisioningJobOutput,
        handler=handle_retry_provisioning_job,
    )
    register_tool(
        mcp,
        name="enqueue_provisioning_job",
        description=(
            "Queue provisioning for a subscription whose payment succeeded but whose "
            "provisioning was never started."
        ),
        input_model=EnqueueProvisioningJobInput,
        output_model=EnqueueProvisioningJobOutput,
        handler=handle_enqueue_provisioning_job,
    )
    register_tool(
        mcp,
        name="resend_activation_notification",
        description="Resend an activation (or other template) notification to a customer.",
        input_model=ResendActivationNotificationInput,
        output_model=ResendActivationNotificationOutput,
        handler=handle_resend_activation_notification,
    )
    register_tool(
        mcp,
        name="apply_outage_credit",
        description="Apply a one-time goodwill credit to a subscription (server-side capped).",
        input_model=ApplyOutageCreditInput,
        output_model=ApplyOutageCreditOutput,
        handler=handle_apply_outage_credit,
    )
    register_tool(
        mcp,
        name="request_refund",
        description="Request a payment refund (always denied: partner-integration lacks billing:refund).",
        input_model=RequestRefundInput,
        output_model=RequestRefundOutput,
        handler=handle_request_refund,
    )
    register_tool(
        mcp,
        name="reschedule_installation",
        description="Reschedule an installation appointment (always denied: partner-integration lacks appointments:write).",
        input_model=RescheduleInstallationInput,
        output_model=RescheduleInstallationOutput,
        handler=handle_reschedule_installation,
    )

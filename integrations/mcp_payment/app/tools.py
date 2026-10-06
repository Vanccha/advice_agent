"""`mcp-payment` tools (docs/contracts.md §3)."""
from __future__ import annotations

from typing import Any

import httpx

from common.result import ToolResult, fail, ok
from common.tool_spec import register_tool

from .deps import payment_api_client
from .diag import DiagQueryError, query_diag
from .duplicates import find_duplicate_groups
from .models import (
    Charge,
    DetectDuplicateChargesInput,
    DetectDuplicateChargesOutput,
    DuplicateChargeGroup,
    GetGatewayHealthInput,
    GetGatewayHealthOutput,
    GetPaymentStatusInput,
    GetPaymentStatusOutput,
    ListCustomerChargesInput,
    ListCustomerChargesOutput,
    PaymentStatus,
)

SOURCE_DIAG = "diag_db"
SOURCE_PAYMENT = "payment_api"

_PAYMENT_STATUS_SQL = """
    SELECT customer_no, subscription_id, payment_id, charge_ref, amount_gbp,
           status, method, failure_code, failure_message, created_at, updated_at
    FROM diag.payment_status
    WHERE (CAST(:customer_no AS text) IS NULL OR customer_no = CAST(:customer_no AS text))
      AND (CAST(:subscription_id AS bigint) IS NULL OR subscription_id = CAST(:subscription_id AS bigint))
      AND (CAST(:payment_id AS bigint) IS NULL OR payment_id = CAST(:payment_id AS bigint))
    ORDER BY created_at DESC
"""


async def handle_get_payment_status(inp: GetPaymentStatusInput) -> ToolResult[Any]:
    if not (inp.customer_no or inp.subscription_id or inp.payment_id):
        return fail("INVALID_INPUT", "provide customer_no, subscription_id, or payment_id", SOURCE_DIAG)
    try:
        rows = query_diag(
            _PAYMENT_STATUS_SQL,
            {
                "customer_no": inp.customer_no,
                "subscription_id": inp.subscription_id,
                "payment_id": inp.payment_id,
            },
        )
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    payments = [PaymentStatus(**row) for row in rows]
    return ok(GetPaymentStatusOutput(payments=payments), SOURCE_DIAG)


async def _list_charges(customer_no: str, status: str | None, limit: int, offset: int) -> ToolResult[Any]:
    params: dict[str, Any] = {"customer_ref": customer_no, "limit": limit, "offset": offset}
    if status is not None:
        params["status"] = status
    return await payment_api_client().get("/psp/v1/charges", SOURCE_PAYMENT, params=params)


async def handle_list_customer_charges(inp: ListCustomerChargesInput) -> ToolResult[Any]:
    result = await _list_charges(inp.customer_no, inp.status, inp.limit, inp.offset)
    if not result.ok:
        return result
    body = result.data or {}
    charges = [Charge(**item) for item in body.get("items", [])]
    return ok(ListCustomerChargesOutput(charges=charges, total=body.get("total", len(charges))), SOURCE_PAYMENT)


async def handle_get_gateway_health(_inp: GetGatewayHealthInput) -> ToolResult[Any]:
    """Report the PSP's reachability and `outage` flag truthfully; never raise,
    even when the gateway is completely down (docs/contracts.md §3). This is a
    diagnostic, so a down gateway is still a *successful* diagnosis — `ok=True`
    with `data.outage=True` / `data.reachable=False` — not a tool failure.
    """
    client = payment_api_client()

    reachable = False
    health_status: int | None = None
    try:
        health_response = await client.request_raw("GET", "/health")
        health_status = health_response.status_code
        reachable = health_status == 200
    except Exception:  # noqa: BLE001 - a down gateway must never raise out of this tool
        reachable = False

    outage = not reachable
    failure_rate: float | None = None
    latency_ms: int | None = None
    force_failure_code: str | None = None
    detail: str | None = None
    try:
        control_response = await client.request_raw("GET", "/psp/v1/control")
        if control_response.status_code == 200:
            body = control_response.json()
            outage = bool(body.get("outage", outage))
            failure_rate = body.get("failure_rate")
            latency_ms = body.get("latency_ms")
            force_failure_code = body.get("force_failure_code")
        else:
            detail = f"control endpoint returned HTTP {control_response.status_code}"
    except Exception as exc:  # noqa: BLE001 - control must never raise out of this tool either
        detail = f"control endpoint unreachable: {exc}"
        outage = True

    return ok(
        GetGatewayHealthOutput(
            reachable=reachable,
            health_http_status=health_status,
            outage=outage,
            failure_rate=failure_rate,
            latency_ms=latency_ms,
            force_failure_code=force_failure_code,
            detail=detail,
        ),
        SOURCE_PAYMENT,
    )


async def handle_detect_duplicate_charges(inp: DetectDuplicateChargesInput) -> ToolResult[Any]:
    result = await _list_charges(inp.customer_no, "succeeded", 200, 0)
    if not result.ok:
        return result
    body = result.data or {}
    groups = find_duplicate_groups(body.get("items", []), inp.window_minutes)
    return ok(
        DetectDuplicateChargesOutput(
            customer_no=inp.customer_no,
            window_minutes=inp.window_minutes,
            duplicate_groups=[DuplicateChargeGroup(**g) for g in groups],
        ),
        SOURCE_PAYMENT,
    )


def register_tools(mcp: Any) -> None:
    register_tool(
        mcp,
        name="get_payment_status",
        description="Get payment/charge status by customer_no, subscription_id, or payment_id.",
        input_model=GetPaymentStatusInput,
        output_model=GetPaymentStatusOutput,
        handler=handle_get_payment_status,
    )
    register_tool(
        mcp,
        name="list_customer_charges",
        description="List a customer's PSP charges, optionally filtered by status.",
        input_model=ListCustomerChargesInput,
        output_model=ListCustomerChargesOutput,
        handler=handle_list_customer_charges,
    )
    register_tool(
        mcp,
        name="get_gateway_health",
        description="Check payment gateway reachability and its outage/failure-rate control flags.",
        input_model=GetGatewayHealthInput,
        output_model=GetGatewayHealthOutput,
        handler=handle_get_gateway_health,
    )
    register_tool(
        mcp,
        name="detect_duplicate_charges",
        description="Find groups of same-amount succeeded charges for a customer within a time window.",
        input_model=DetectDuplicateChargesInput,
        output_model=DetectDuplicateChargesOutput,
        handler=handle_detect_duplicate_charges,
    )

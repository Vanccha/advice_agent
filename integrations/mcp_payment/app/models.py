"""Pydantic input/output models for every `mcp-payment` tool
(docs/contracts.md §3)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# get_payment_status (diag.payment_status)
# --------------------------------------------------------------------------


class GetPaymentStatusInput(BaseModel):
    customer_no: Optional[str] = None
    subscription_id: Optional[int] = None
    payment_id: Optional[int] = None


class PaymentStatus(BaseModel):
    customer_no: str
    subscription_id: int
    payment_id: int
    charge_ref: str
    amount_try: float
    status: str
    method: str
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class GetPaymentStatusOutput(BaseModel):
    payments: list[PaymentStatus]


# --------------------------------------------------------------------------
# list_customer_charges (payment REST GET /psp/v1/charges)
# --------------------------------------------------------------------------


class ListCustomerChargesInput(BaseModel):
    customer_no: str
    status: Optional[str] = Field(
        default=None, description="pending|succeeded|failed|refunded|partially_refunded"
    )
    limit: int = 50
    offset: int = 0


class Charge(BaseModel):
    charge_ref: str
    status: str
    customer_ref: str
    amount_try: float
    currency: str
    method: str
    card_last4: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    idempotency_key: str
    created_at: str
    updated_at: str


class ListCustomerChargesOutput(BaseModel):
    charges: list[Charge]
    total: int


# --------------------------------------------------------------------------
# get_gateway_health (payment /psp/v1/control + /health)
# --------------------------------------------------------------------------


class GetGatewayHealthInput(BaseModel):
    pass


class GetGatewayHealthOutput(BaseModel):
    reachable: bool = Field(description="whether GET /health returned 200")
    health_http_status: Optional[int] = None
    outage: bool = Field(description="the PSP's own `outage` control flag")
    failure_rate: Optional[float] = None
    latency_ms: Optional[int] = None
    force_failure_code: Optional[str] = None
    detail: Optional[str] = Field(
        default=None, description="human-readable note, e.g. when control itself was unreachable"
    )


# --------------------------------------------------------------------------
# detect_duplicate_charges (payment REST, pure detection logic in duplicates.py)
# --------------------------------------------------------------------------


class DetectDuplicateChargesInput(BaseModel):
    customer_no: str
    window_minutes: int = Field(default=60, ge=1, le=24 * 60)


class DuplicateChargeGroup(BaseModel):
    amount_try: float
    charge_refs: list[str]
    timestamps: list[str]
    count: int


class DetectDuplicateChargesOutput(BaseModel):
    customer_no: str
    window_minutes: int
    duplicate_groups: list[DuplicateChargeGroup]

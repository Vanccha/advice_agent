from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChargeCreateRequest(BaseModel):
    amount_try: float = Field(gt=0)
    currency: str = "TRY"
    customer_ref: str
    method: Literal["card", "eft"]
    card_token: str | None = None
    idempotency_key: str
    callback_url: str | None = None


class ChargeResponse(BaseModel):
    charge_ref: str
    status: str
    customer_ref: str
    amount_try: float
    currency: str = "TRY"
    method: str
    card_last4: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    idempotency_key: str
    created_at: str
    updated_at: str


class ChargeListResponse(BaseModel):
    items: list[ChargeResponse]
    total: int


class RefundCreateRequest(BaseModel):
    amount_try: float = Field(gt=0)
    reason: str | None = None


class RefundResponse(BaseModel):
    refund_ref: str
    charge_ref: str
    amount_try: float
    status: str
    reason: str | None = None
    created_at: str
    charge_status: str


class ControlFlagsPayload(BaseModel):
    failure_rate: float | None = Field(default=None, ge=0, le=1)
    outage: bool | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    force_failure_code: str | None = None


class ControlFlagsResponse(BaseModel):
    failure_rate: float
    outage: bool
    latency_ms: int
    force_failure_code: str | None = None

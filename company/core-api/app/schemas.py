from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    full_name: str
    national_id: str = Field(min_length=11, max_length=11)
    phone: str
    email: str
    address_line: str
    district: str
    city: str
    region_code: str
    kvkk_consent: bool = True


class SubscriptionCreate(BaseModel):
    customer_no: str
    package_code: str


class PaymentCreate(BaseModel):
    amount_try: float | None = None
    method: str = "card"
    card_token: str | None = None
    idempotency_key: str


class TransitionRequest(BaseModel):
    to_status: str
    reason: str | None = None


class CancelRequest(BaseModel):
    reason: str | None = None


class RefundRequest(BaseModel):
    payment_id: int
    amount_try: float
    reason: str


class CreditRequest(BaseModel):
    subscription_id: int
    amount_try: float
    reason: str
    idempotency_key: str


class RescheduleRequest(BaseModel):
    scheduled_date: date
    time_slot: str


class NotificationResendRequest(BaseModel):
    customer_no: str
    template_code: str
    channel: str = "sms"

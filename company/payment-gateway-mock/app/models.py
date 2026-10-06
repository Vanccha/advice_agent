from __future__ import annotations

from datetime import datetime

from sqlalchemy import Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.clock import utcnow
from shared.db import Base

SCHEMA = "psp"


class Charge(Base):
    __tablename__ = "charges"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    charge_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_ref: Mapped[str] = mapped_column(String(64), index=True)
    amount_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(16))
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    charge_id: Mapped[int] = mapped_column(index=True)
    amount_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    charge_id: Mapped[int] = mapped_column(index=True)
    target_url: Mapped[str] = mapped_column(String(255))
    event: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(default=1)
    response_status: Mapped[int | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ControlFlag(Base):
    __tablename__ = "control_flags"
    __table_args__ = {"schema": SCHEMA}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | float | bool | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.clock import utcnow
from shared.db import Base

SCHEMA = "tkt"


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    channel_slug: Mapped[str] = mapped_column(String(64), nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        Index(
            "ix_tickets_external_ref_unique",
            "external_ref",
            unique=True,
            postgresql_where="external_ref IS NOT NULL",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW", index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    requester_customer_no: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    requester_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    requester_contact: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    incident_ref: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempted_steps: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    suggested_next_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_customers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Not listed in docs/contracts.md §1.3's column list, but §4.1's structured payload
    # includes it and the task requires every payload field to be stored. Additive column,
    # does not conflict with any documented behaviour. See final report for this deviation.
    urgency_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    status_history: Mapped[list["StatusHistory"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="StatusHistory.created_at"
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.tickets.id"), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="comments")


class StatusHistory(Base):
    __tablename__ = "status_history"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.tickets.id"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="status_history")


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    events: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    secret: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.webhook_subscriptions.id"), nullable=False, index=True
    )
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.tickets.id"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

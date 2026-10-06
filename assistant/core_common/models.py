"""SQLAlchemy 2.0 ORM models for schema `asst` (contracts §1.5).

Other agents (decision/modes/api layers) import these directly, e.g.
``from core_common.models import Conversation, AuditEntryRow``.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSON under Postgres is `jsonb` (contracts); plain JSON under SQLite (unit tests).
JSONType = JSONB().with_variant(JSON(), "sqlite")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    tenant: Mapped[str] = mapped_column(String, nullable=False)
    customer_no: Mapped[str | None] = mapped_column(String, nullable=True)
    masked_customer_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    mode: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str | None] = mapped_column(String, nullable=True)
    masked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditEntryRow(Base):
    """Append-only (enforced by a DB trigger — see `db.AUDIT_APPEND_ONLY_TRIGGER_SQL`)."""

    __tablename__ = "audit_entries"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    tenant: Mapped[str] = mapped_column(String, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    policy_decision: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_input: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    tool_output_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stored as an ISO-8601 string (not DateTime) deliberately: this value is part of the
    # hashed payload, and must read back byte-for-byte identical to what was hashed on
    # insert. A DateTime column round-trips through driver-specific parsing (notably under
    # SQLite) that can alter formatting and silently break `verify_chain`.
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class ActionRecord(Base):
    __tablename__ = "action_records"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    action_name: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    policy_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TicketLink(Base):
    __tablename__ = "ticket_links"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    ticket_key: Mapped[str] = mapped_column(String, nullable=False)
    department: Mapped[str] = mapped_column(String, nullable=False)
    last_known_status: Mapped[str | None] = mapped_column(String, nullable=True)
    notified_status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class PendingApproval(Base):
    __tablename__ = "pending_approvals"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    action_name: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    prompt_en: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = {"schema": "asst"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    alertname: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    labels: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    annotations: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    handling_note: Mapped[str | None] = mapped_column(Text, nullable=True)


ALL_TABLES = (
    Conversation,
    Message,
    AuditEntryRow,
    ActionRecord,
    TicketLink,
    PendingApproval,
    AlertEvent,
)

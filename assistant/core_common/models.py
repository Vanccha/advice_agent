"""SQLAlchemy 2.0 ORM models for the ``asst`` schema.

Where this sits: ``core_common``. Imported by ``db.py`` (table creation), ``audit.log``,
``tickets.client`` and ``modes``; for example ``from core_common.models import Conversation``.

Key ideas:
- Typed ``Mapped[...]`` columns: the table definition doubles as documentation.
- JSON columns are ``jsonb`` on Postgres and plain JSON on SQLite, so tests need no Postgres.
- ``AuditEntryRow.created_at`` is a string on purpose (it is part of the hashed payload).

Read next: ``core_common/db.py``, ``audit/log.py``.
See: docs/contracts.md Â§1.5.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSON under Postgres is `jsonb` (contracts); plain JSON under SQLite (unit tests).
JSONType = JSONB().with_variant(JSON(), "sqlite")


def _utcnow() -> dt.datetime:
    """Timezone-aware UTC now, the default for every `created_at` column."""
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    """Declarative base shared by every table in the `asst` schema."""


class Conversation(Base):
    """One chat conversation and its state-machine state."""

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
    """One chat turn as shown to the customer; `masked` says whether `content` was masked."""

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
    # An ISO-8601 string, not DateTime, on purpose: the value is part of the hashed payload and
    # must read back byte-for-byte as hashed. A DateTime column round-trips through
    # driver-specific parsing (notably SQLite) that can change the format and silently break
    # `verify_chain`.
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class ActionRecord(Base):
    """Every action attempt, including denied or unconfirmed ones (`executed` says which)."""

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
    """Links a conversation to a ticket and tracks which status the customer was last told."""

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
    """An irreversible action waiting for the customer to confirm or decline."""

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
    """A monitoring alert received by the webhook, with whether it was handled."""

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


# Every ORM table, for code that needs to iterate them (tests, maintenance scripts).
ALL_TABLES = (
    Conversation,
    Message,
    AuditEntryRow,
    ActionRecord,
    TicketLink,
    PendingApproval,
    AlertEvent,
)

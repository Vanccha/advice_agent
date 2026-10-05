from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.clock import utcnow
from shared.db import Base

SCHEMA = "notify"

SEVERITIES = ("info", "warning", "critical")


class Channel(Base):
    """A department/topic channel (Teams/Slack-style)."""

    __tablename__ = "channels"
    __table_args__ = {"schema": SCHEMA}

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    """One posted message in a channel."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("severity in ('info','warning','critical')", name="ck_messages_severity"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_slug: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.channels.slug"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    external_ref: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    channel: Mapped["Channel"] = relationship(back_populates="messages")

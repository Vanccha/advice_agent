"""Prometheus metrics for the hub: messages by channel and severity.

Where this sits: updated whenever a message is stored; scraped via `/metrics`.

Key ideas:
- A counter records the rate of messages; a gauge, recomputed from the
  database, records the stored total so it survives restarts.

Read next: `routers/api.py`.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Message

notify_messages_total = Counter(
    "notify_messages_total",
    "Messages posted into a notification-hub channel.",
    ["channel", "severity"],
)

notify_channel_messages = Gauge(
    "notify_channel_messages",
    "Current total message count stored per channel.",
    ["channel"],
)


def record_message(session: Session, channel_slug: str, severity: str) -> None:
    """Update counters and gauges after a message has been added and flushed."""
    notify_messages_total.labels(channel=channel_slug, severity=severity).inc()
    total = session.scalar(
        select(func.count()).select_from(Message).where(Message.channel_slug == channel_slug)
    )
    notify_channel_messages.labels(channel=channel_slug).set(total or 0)

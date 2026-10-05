"""Pydantic input/output models for every `mcp-notification` tool
(docs/contracts.md §3, §1.4, §2.6)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "critical"]


class MessageDetail(BaseModel):
    """Mirrors notification-hub's message response field-for-field."""

    id: int
    channel_slug: str
    title: str
    text: str
    severity: str
    source: str
    fields: dict[str, Any] = Field(default_factory=dict)
    external_ref: Optional[str] = None
    created_at: str


# --------------------------------------------------------------------------
# post_department_message
# --------------------------------------------------------------------------


class PostDepartmentMessageInput(BaseModel):
    channel: str = Field(description="channel slug, e.g. 'faturalama', 'teknik-altyapi'")
    title: str
    text: str
    severity: Severity = "info"
    fields: Optional[dict[str, Any]] = None
    external_ref: Optional[str] = None


class PostDepartmentMessageOutput(BaseModel):
    message: MessageDetail


# --------------------------------------------------------------------------
# list_channel_messages
# --------------------------------------------------------------------------


class ListChannelMessagesInput(BaseModel):
    channel: str
    limit: int = 50


class ListChannelMessagesOutput(BaseModel):
    messages: list[MessageDetail]
    total: int

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.auth import ApiKeyRegistry, principal_dependency
from shared.clock import isoformat, utcnow
from shared.errors import NotFound

from app.db import get_session
from app.metrics_gauges import record_message
from app.models import Channel, Message
from app.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["notify"])

_registry = ApiKeyRegistry()
_registry.register(get_settings().notify_api_key, name="notify-client", scopes={"*"})
require_api_key = principal_dependency(_registry)


class MessageIn(BaseModel):
    title: str
    text: str
    severity: Literal["info", "warning", "critical"] = "info"
    source: str = "manual"
    fields: dict = Field(default_factory=dict)
    external_ref: str | None = None


def _channel_or_404(session: Session, slug: str) -> Channel:
    channel = session.get(Channel, slug)
    if channel is None:
        raise NotFound("CHANNEL_NOT_FOUND", f"Channel '{slug}' was not found.", slug=slug)
    return channel


def _message_payload(message: Message) -> dict:
    return {
        "id": message.id,
        "channel_slug": message.channel_slug,
        "title": message.title,
        "text": message.text,
        "severity": message.severity,
        "source": message.source,
        "fields": message.fields,
        "external_ref": message.external_ref,
        "created_at": isoformat(message.created_at),
    }


def _channel_payload(channel: Channel) -> dict:
    return {
        "slug": channel.slug,
        "display_name": channel.display_name,
        "description": channel.description,
    }


@router.post("/channels/{slug}/messages", status_code=201)
def post_message(
    slug: str,
    body: MessageIn,
    session: Session = Depends(get_session),
    _principal=Depends(require_api_key),
) -> dict:
    _channel_or_404(session, slug)
    message = Message(
        channel_slug=slug,
        title=body.title,
        text=body.text,
        severity=body.severity,
        source=body.source,
        fields=body.fields,
        external_ref=body.external_ref,
        created_at=utcnow(),
    )
    session.add(message)
    session.flush()
    record_message(session, slug, body.severity)
    return _message_payload(message)


@router.get("/channels/{slug}/messages")
def list_messages(
    slug: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    _principal=Depends(require_api_key),
) -> dict:
    _channel_or_404(session, slug)
    stmt = (
        select(Message)
        .where(Message.channel_slug == slug)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = session.execute(stmt).scalars().all()
    total = session.scalar(
        select(func.count()).select_from(Message).where(Message.channel_slug == slug)
    )
    return {"items": [_message_payload(m) for m in items], "total": total or 0}


@router.get("/channels")
def list_channels(
    session: Session = Depends(get_session),
    _principal=Depends(require_api_key),
) -> dict:
    items = session.execute(select(Channel).order_by(Channel.slug)).scalars().all()
    return {"items": [_channel_payload(c) for c in items], "total": len(items)}

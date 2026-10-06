from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.errors import NotFound

from app.db import get_session
from app.models import Channel, Message

router = APIRouter(tags=["ui"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _initials(name: str) -> str:
    parts = [p for p in name.replace("-", " ").split() if p]
    letters = "".join(p[0] for p in parts[:2]).upper()
    return letters or "?"


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y %H:%M")


def _to_pretty_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(value)


templates.env.filters["initials"] = _initials
templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.filters["to_pretty_json"] = _to_pretty_json


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    channels = session.execute(select(Channel).order_by(Channel.slug)).scalars().all()
    rows = []
    for channel in channels:
        count = (
            session.scalar(
                select(func.count()).select_from(Message).where(Message.channel_slug == channel.slug)
            )
            or 0
        )
        latest = session.execute(
            select(Message)
            .where(Message.channel_slug == channel.slug)
            .order_by(Message.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        rows.append({"channel": channel, "count": count, "latest": latest})
    return templates.TemplateResponse(request, "index.html", {"rows": rows})


@router.get("/c/{slug}")
def channel_stream(slug: str, request: Request, session: Session = Depends(get_session)):
    channel = session.get(Channel, slug)
    if channel is None:
        raise NotFound("CHANNEL_NOT_FOUND", f"Channel '{slug}' was not found.", slug=slug)
    messages = session.execute(
        select(Message).where(Message.channel_slug == slug).order_by(Message.created_at.desc()).limit(200)
    ).scalars().all()
    return templates.TemplateResponse(
        request, "channel.html", {"channel": channel, "messages": messages}
    )

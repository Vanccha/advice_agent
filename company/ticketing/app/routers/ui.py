from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from shared.clock import utcnow

from app.deps import get_session
from app.services import add_comment, change_status, department_summary, get_ticket_by_key, list_tickets
from app.transitions import ALLOWED_TRANSITIONS

router = APIRouter(include_in_schema=False)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

DEPARTMENT_LABELS = {
    "TECHNICAL_INFRA": "Technical Infrastructure",
    "BILLING": "Billing",
    "SUBSCRIPTION_OPS": "Subscription Operations",
    "FIELD_INSTALL": "Field Installation Team",
}

STATUS_LABELS = {
    "NEW": "New",
    "TRIAGE": "Triage",
    "IN_PROGRESS": "In Progress",
    "WAITING_CUSTOMER": "Waiting for Customer",
    "RESOLVED": "Resolved",
    "CLOSED": "Closed",
    "REJECTED": "Rejected",
}

PRIORITY_LABELS = {
    "LOW": "Low",
    "NORMAL": "Normal",
    "HIGH": "High",
    "URGENT": "Urgent",
}


@router.get("/agent", response_class=HTMLResponse)
def agent_ticket_list(
    request: Request,
    department: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    limit = max(1, min(limit, 200))
    items, total = list_tickets(
        session,
        department=department or None,
        status=status or None,
        search=q or None,
        limit=limit,
        offset=offset,
    )
    sidebar = department_summary(session)
    now = utcnow()
    return templates.TemplateResponse(
        request,
        "agent_list.html",
        {
            "tickets": items,
            "total": total,
            "sidebar": sidebar,
            "department": department or "",
            "status": status or "",
            "q": q or "",
            "limit": limit,
            "offset": offset,
            "now": now,
            "department_labels": DEPARTMENT_LABELS,
            "status_labels": STATUS_LABELS,
            "priority_labels": PRIORITY_LABELS,
            "all_departments": DEPARTMENT_LABELS,
            "all_statuses": STATUS_LABELS,
        },
    )


@router.get("/agent/tickets/{ticket_key}", response_class=HTMLResponse)
def agent_ticket_detail(
    request: Request,
    ticket_key: str,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    ticket = get_ticket_by_key(session, ticket_key)
    sidebar = department_summary(session)
    now = utcnow()
    allowed_next = sorted(ALLOWED_TRANSITIONS.get(ticket.status, set()))
    return templates.TemplateResponse(
        request,
        "agent_detail.html",
        {
            "ticket": ticket,
            "sidebar": sidebar,
            "now": now,
            "department_labels": DEPARTMENT_LABELS,
            "status_labels": STATUS_LABELS,
            "priority_labels": PRIORITY_LABELS,
            "allowed_next": allowed_next,
        },
    )


async def _read_urlencoded_form(request: Request) -> dict[str, str]:
    """Parse `application/x-www-form-urlencoded` bodies without `python-multipart`.

    We only ever submit plain urlencoded forms from our own templates (no file
    uploads), so we parse the raw body ourselves instead of depending on FastAPI's
    `Form(...)` marker, which unconditionally requires `python-multipart` to be
    installed even for urlencoded bodies -- an extra dependency this service does
    not otherwise need.
    """
    raw = await request.body()
    return dict(parse_qsl(raw.decode("utf-8")))


@router.post("/agent/tickets/{ticket_key}/comments")
async def agent_post_comment(
    request: Request,
    ticket_key: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    form = await _read_urlencoded_form(request)
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    ticket = get_ticket_by_key(session, ticket_key)
    add_comment(
        session,
        settings,
        ticket,
        author=form.get("author") or "agent",
        author_type="agent",
        body=form.get("body", ""),
        is_internal=form.get("is_internal") in ("true", "on", "1"),
        session_factory=session_factory,
        background_tasks=background_tasks,
    )
    return RedirectResponse(url=f"/agent/tickets/{ticket_key}", status_code=303)


@router.post("/agent/tickets/{ticket_key}/status")
async def agent_post_status(
    request: Request,
    ticket_key: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    form = await _read_urlencoded_form(request)
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    ticket = get_ticket_by_key(session, ticket_key)
    change_status(
        session,
        settings,
        ticket,
        form.get("status", ""),
        actor=form.get("actor") or "agent",
        note=form.get("note") or None,
        session_factory=session_factory,
        background_tasks=background_tasks,
    )
    return RedirectResponse(url=f"/agent/tickets/{ticket_key}", status_code=303)

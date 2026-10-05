from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import principal_dependency, require_scope
from shared.errors import NotFound

from app.deps import get_session
from app.models import Department, WebhookSubscription
from app.schemas import (
    CommentCreateRequest,
    CommentResponse,
    DepartmentResponse,
    TicketCreateRequest,
    TicketListResponse,
    TicketPatchRequest,
    TicketResponse,
    WebhookSubscriptionCreateRequest,
    WebhookSubscriptionResponse,
)
from app.services import add_comment, create_ticket, get_ticket_by_key, list_tickets, patch_ticket

router = APIRouter(prefix="/api/v1", tags=["tickets"])


def _require_scope(scope: str):
    """Build a per-request auth dependency against the live registry on app.state.

    `shared.auth.require_scope` is designed to be constructed once with a concrete
    registry and used via `Depends(...)`. The registry here lives on `app.state`
    (so tests can build a fresh app/registry per settings instance), so we resolve it
    lazily per request and call the two underlying dependency functions directly
    (passing explicit arguments, bypassing their `Depends(...)` defaults).
    """

    def _dependency(request: Request):
        registry = request.app.state.api_key_registry
        principal = principal_dependency(registry)(request)
        return require_scope(scope, registry)(principal)

    return _dependency


_read_scope = _require_scope("tickets:read")
_write_scope = _require_scope("tickets:write")


@router.post("/tickets", response_model=TicketResponse)
def post_ticket(
    payload: TicketCreateRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    principal=Depends(_write_scope),
) -> object:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    ticket, created = create_ticket(
        session,
        settings,
        payload,
        session_factory=session_factory,
        background_tasks=background_tasks,
    )
    response.status_code = 201 if created else 200
    return ticket


@router.get("/tickets", response_model=TicketListResponse)
def get_tickets(
    request: Request,
    department: str | None = None,
    status: str | None = None,
    customer_no: str | None = None,
    incident_ref: str | None = None,
    external_ref: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    principal=Depends(_read_scope),
) -> object:
    limit = max(1, min(limit, 200))
    items, total = list_tickets(
        session,
        department=department,
        status=status,
        customer_no=customer_no,
        incident_ref=incident_ref,
        external_ref=external_ref,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@router.get("/tickets/{ticket_key}", response_model=TicketResponse)
def get_ticket(
    ticket_key: str,
    session: Session = Depends(get_session),
    principal=Depends(_read_scope),
) -> object:
    return get_ticket_by_key(session, ticket_key)


@router.patch("/tickets/{ticket_key}", response_model=TicketResponse)
def patch_ticket_endpoint(
    ticket_key: str,
    payload: TicketPatchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    principal=Depends(_write_scope),
) -> object:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    ticket = get_ticket_by_key(session, ticket_key)
    return patch_ticket(
        session,
        settings,
        ticket,
        status=payload.status,
        assignee=payload.assignee,
        department=payload.department,
        priority=payload.priority,
        note=payload.note,
        actor=principal.name,
        session_factory=session_factory,
        background_tasks=background_tasks,
    )


@router.post("/tickets/{ticket_key}/comments", response_model=CommentResponse)
def post_comment(
    ticket_key: str,
    payload: CommentCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    principal=Depends(_write_scope),
) -> object:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    ticket = get_ticket_by_key(session, ticket_key)
    return add_comment(
        session,
        settings,
        ticket,
        author=payload.author,
        author_type=payload.author_type,
        body=payload.body,
        is_internal=payload.is_internal,
        session_factory=session_factory,
        background_tasks=background_tasks,
    )


@router.get("/departments", response_model=list[DepartmentResponse])
def get_departments(
    session: Session = Depends(get_session),
    principal=Depends(_read_scope),
) -> object:
    return session.execute(select(Department)).scalars().all()


@router.get("/webhook-subscriptions", response_model=list[WebhookSubscriptionResponse])
def get_webhook_subscriptions(
    session: Session = Depends(get_session),
    principal=Depends(_read_scope),
) -> object:
    return session.execute(select(WebhookSubscription)).scalars().all()


@router.post("/webhook-subscriptions", response_model=WebhookSubscriptionResponse)
def post_webhook_subscription(
    payload: WebhookSubscriptionCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
    principal=Depends(_write_scope),
) -> object:
    settings = request.app.state.settings
    subscription = WebhookSubscription(
        name=payload.name,
        target_url=payload.target_url,
        events=payload.events,
        is_active=payload.is_active,
        secret=payload.secret or settings.webhook_secret,
    )
    session.add(subscription)
    session.flush()
    session.refresh(subscription)
    return subscription


@router.delete("/webhook-subscriptions/{subscription_id}", status_code=204)
def delete_webhook_subscription(
    subscription_id: int,
    session: Session = Depends(get_session),
    principal=Depends(_write_scope),
) -> None:
    subscription = session.get(WebhookSubscription, subscription_id)
    if subscription is None:
        raise NotFound(
            "WEBHOOK_SUBSCRIPTION_NOT_FOUND",
            f"No webhook subscription with id {subscription_id}.",
            subscription_id=subscription_id,
        )
    session.delete(subscription)

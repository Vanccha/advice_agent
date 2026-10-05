from __future__ import annotations

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from shared.clock import utcnow
from shared.errors import NotFound

from app.keys import next_ticket_key
from app.metrics_gauges import record_ticket_event, refresh_open_ticket_gauges
from app.models import Comment, Department, StatusHistory, Ticket
from app.notify_client import notify_ticket_created, notify_ticket_resolved
from app.schemas import Requester, TicketCreateRequest
from app.settings import TicketingSettings
from app.sla import sla_due_at
from app.transitions import OPEN_STATUSES, check_transition
from app.webhooks import build_webhook_payload, deliver_event


def department_summary(session: Session) -> list[dict]:
    """Departments with their current open-ticket count, for the /agent sidebar."""
    from sqlalchemy import func

    counts = dict(
        session.execute(
            select(Ticket.department, func.count(Ticket.id))
            .where(Ticket.status.in_(OPEN_STATUSES))
            .group_by(Ticket.department)
        ).all()
    )
    departments = session.execute(select(Department).order_by(Department.code)).scalars().all()
    return [
        {"code": d.code, "display_name": d.display_name, "open_count": counts.get(d.code, 0)}
        for d in departments
    ]


def _schedule_webhook(
    background_tasks: BackgroundTasks | None,
    session_factory: sessionmaker[Session],
    ticket_id: int,
    event: str,
    payload: dict,
) -> None:
    if background_tasks is not None:
        background_tasks.add_task(deliver_event, session_factory, ticket_id, event, payload)
    else:
        # No background-task queue available (e.g. called from a plain function);
        # deliver synchronously rather than silently dropping the event.
        deliver_event(session_factory, ticket_id, event, payload)


def get_ticket_by_key(session: Session, ticket_key: str) -> Ticket:
    ticket = session.execute(
        select(Ticket)
        .options(selectinload(Ticket.comments), selectinload(Ticket.status_history))
        .where(Ticket.ticket_key == ticket_key)
    ).scalar_one_or_none()
    if ticket is None:
        raise NotFound("TICKET_NOT_FOUND", f"No ticket with key '{ticket_key}'.", ticket_key=ticket_key)
    return ticket


def find_ticket_by_external_ref(session: Session, external_ref: str) -> Ticket | None:
    return session.execute(
        select(Ticket)
        .options(selectinload(Ticket.comments), selectinload(Ticket.status_history))
        .where(Ticket.external_ref == external_ref)
    ).scalar_one_or_none()


def list_tickets(
    session: Session,
    *,
    department: str | None = None,
    status: str | None = None,
    customer_no: str | None = None,
    incident_ref: str | None = None,
    external_ref: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Ticket], int]:
    stmt = select(Ticket)
    if department:
        stmt = stmt.where(Ticket.department == department)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if customer_no:
        stmt = stmt.where(Ticket.requester_customer_no == customer_no)
    if incident_ref:
        stmt = stmt.where(Ticket.incident_ref == incident_ref)
    if external_ref:
        stmt = stmt.where(Ticket.external_ref == external_ref)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            (Ticket.requester_customer_no.ilike(like)) | (Ticket.subject.ilike(like))
        )

    total = len(session.execute(stmt).scalars().all())
    stmt = stmt.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)
    items = session.execute(stmt).scalars().all()
    return list(items), total


def create_ticket(
    session: Session,
    settings: TicketingSettings,
    payload: TicketCreateRequest,
    *,
    session_factory: sessionmaker[Session],
    background_tasks: BackgroundTasks | None = None,
) -> tuple[Ticket, bool]:
    """Create a ticket, idempotent on `external_ref`.

    Returns (ticket, created) where `created` is False when an existing ticket with the
    same `external_ref` was returned instead of inserting a new row.
    """
    if payload.external_ref:
        existing = find_ticket_by_external_ref(session, payload.external_ref)
        if existing is not None:
            return existing, False

    requester = payload.requester or Requester()
    now = utcnow()
    ticket = Ticket(
        ticket_key=next_ticket_key(session),
        department=payload.department,
        status="NEW",
        priority=payload.priority,
        issue_type=payload.issue_type,
        subject=payload.subject,
        body=payload.body,
        requester_customer_no=requester.customer_no,
        requester_name=requester.name,
        requester_contact=requester.contact,
        source=payload.source,
        external_ref=payload.external_ref,
        incident_ref=payload.incident_ref,
        evidence=payload.evidence,
        attempted_steps=payload.attempted_steps,
        suggested_next_step=payload.suggested_next_step,
        affected_customers=payload.affected_customers,
        urgency_reason=payload.urgency_reason,
        sla_due_at=sla_due_at(payload.priority, now),
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.flush()

    session.add(
        StatusHistory(
            ticket_id=ticket.id,
            from_status=None,
            to_status="NEW",
            actor="system",
            note="Talep oluşturuldu.",
        )
    )
    # Commit now (not just flush): the webhook delivery below runs as a background
    # task against a brand-new session/connection (possibly after this request's own
    # session has committed and closed, possibly concurrently -- FastAPI runs a
    # yield-dependency's post-yield cleanup, i.e. our commit in `get_session`, only
    # *after* background tasks finish). Without an explicit commit here, that second
    # session would not yet see this ticket row and webhook_deliveries' FK would fail.
    session.commit()

    record_ticket_event(ticket.department, ticket.status)
    refresh_open_ticket_gauges(session)

    ticket_id = ticket.id
    webhook_payload = build_webhook_payload(event="ticket.created", ticket=ticket, old_status=None)
    _schedule_webhook(background_tasks, session_factory, ticket_id, "ticket.created", webhook_payload)

    notify_ticket_created(settings, ticket)

    # eager-load relationships for the response before the session closes
    session.refresh(ticket, attribute_names=["comments", "status_history"])
    return ticket, True


def change_status(
    session: Session,
    settings: TicketingSettings,
    ticket: Ticket,
    new_status: str,
    *,
    actor: str,
    note: str | None,
    session_factory: sessionmaker[Session],
    background_tasks: BackgroundTasks | None = None,
) -> Ticket:
    old_status = ticket.status
    if new_status != old_status:
        check_transition(old_status, new_status)
        ticket.status = new_status
        if new_status == "RESOLVED":
            ticket.resolved_at = utcnow()
        session.add(
            StatusHistory(
                ticket_id=ticket.id,
                from_status=old_status,
                to_status=new_status,
                actor=actor,
                note=note,
            )
        )
        ticket.updated_at = utcnow()
        # Commit before scheduling the webhook background task -- see the comment in
        # `create_ticket` for why this must happen before `_schedule_webhook`.
        session.commit()

        record_ticket_event(ticket.department, new_status)
        refresh_open_ticket_gauges(session)

        webhook_payload = build_webhook_payload(
            event="ticket.status_changed",
            ticket=ticket,
            old_status=old_status,
            new_status=new_status,
            comment=note,
        )
        _schedule_webhook(
            background_tasks, session_factory, ticket.id, "ticket.status_changed", webhook_payload
        )

        if new_status == "RESOLVED":
            notify_ticket_resolved(settings, ticket)

    session.flush()
    session.refresh(ticket, attribute_names=["comments", "status_history"])
    return ticket


def patch_ticket(
    session: Session,
    settings: TicketingSettings,
    ticket: Ticket,
    *,
    status: str | None,
    assignee: str | None,
    department: str | None,
    priority: str | None,
    note: str | None,
    actor: str,
    session_factory: sessionmaker[Session],
    background_tasks: BackgroundTasks | None = None,
) -> Ticket:
    if department is not None:
        ticket.department = department
    if priority is not None:
        ticket.priority = priority
    if assignee is not None:
        ticket.assignee = assignee
    ticket.updated_at = utcnow()
    session.flush()

    if status is not None:
        ticket = change_status(
            session,
            settings,
            ticket,
            status,
            actor=actor,
            note=note,
            session_factory=session_factory,
            background_tasks=background_tasks,
        )
    else:
        session.flush()
        session.refresh(ticket, attribute_names=["comments", "status_history"])
    return ticket


def add_comment(
    session: Session,
    settings: TicketingSettings,
    ticket: Ticket,
    *,
    author: str,
    author_type: str,
    body: str,
    is_internal: bool,
    session_factory: sessionmaker[Session],
    background_tasks: BackgroundTasks | None = None,
) -> Comment:
    comment = Comment(
        ticket_id=ticket.id,
        author=author,
        author_type=author_type,
        body=body,
        is_internal=is_internal,
    )
    session.add(comment)
    ticket.updated_at = utcnow()
    # Commit before scheduling the webhook background task -- see the comment in
    # `create_ticket` for why this must happen before `_schedule_webhook`.
    session.commit()
    session.refresh(comment)

    webhook_payload = build_webhook_payload(
        event="ticket.commented",
        ticket=ticket,
        old_status=ticket.status,
        new_status=ticket.status,
        comment=body,
    )
    _schedule_webhook(background_tasks, session_factory, ticket.id, "ticket.commented", webhook_payload)

    return comment

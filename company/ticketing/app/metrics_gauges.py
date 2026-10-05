from __future__ import annotations

from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Department, Ticket
from app.transitions import OPEN_STATUSES

TICKETS_TOTAL = Counter(
    "tkt_tickets_total",
    "Tickets created or moved into a status, by department and status.",
    ["department", "status"],
)

OPEN_TICKETS = Gauge(
    "tkt_open_tickets",
    "Currently open tickets per department (refreshed from the database).",
    ["department"],
)

WEBHOOK_DELIVERIES_TOTAL = Counter(
    "tkt_webhook_deliveries_total",
    "Outgoing webhook deliveries, by final result.",
    ["result"],
)


def record_ticket_event(department: str, status: str) -> None:
    TICKETS_TOTAL.labels(department=department, status=status).inc()


def record_webhook_delivery_result(result: str) -> None:
    WEBHOOK_DELIVERIES_TOTAL.labels(result=result).inc()


def refresh_open_ticket_gauges(session: Session) -> None:
    """Recompute `tkt_open_tickets{department}` straight from the database."""
    rows = session.execute(
        select(Ticket.department, func.count(Ticket.id))
        .where(Ticket.status.in_(OPEN_STATUSES))
        .group_by(Ticket.department)
    ).all()
    counts = {department: count for department, count in rows}
    all_departments = session.execute(select(Department.code)).scalars().all()
    for code in all_departments:
        OPEN_TICKETS.labels(department=code).set(counts.get(code, 0))

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prometheus_client import Gauge

from shared.clock import utcnow

from app.models import InstallationAppointment, NetworkIncident, ProvisioningJob, Subscription

PROVISIONING_JOBS_STUCK = Gauge(
    "nethiz_provisioning_jobs_stuck", "Provisioning jobs currently flagged stuck."
)
OPEN_INCIDENTS = Gauge(
    "nethiz_open_incidents", "Open or monitoring network incidents.", ["severity"]
)
MISSED_APPOINTMENTS_24H = Gauge(
    "nethiz_missed_appointments_24h", "Installation appointments missed in the last 24h."
)
SUBSCRIPTIONS_TOTAL = Gauge(
    "nethiz_subscriptions_total", "Subscriptions by status.", ["status"]
)

ALL_SEVERITIES = ("minor", "major", "critical")


def refresh_gauges(session: Session) -> None:
    stuck = session.scalar(
        select(func.count()).select_from(ProvisioningJob).where(ProvisioningJob.status == "stuck")
    ) or 0
    PROVISIONING_JOBS_STUCK.set(stuck)

    for severity in ALL_SEVERITIES:
        count = session.scalar(
            select(func.count())
            .select_from(NetworkIncident)
            .where(
                NetworkIncident.severity == severity,
                NetworkIncident.status.in_(["open", "monitoring"]),
            )
        ) or 0
        OPEN_INCIDENTS.labels(severity=severity).set(count)

    cutoff = utcnow() - timedelta(hours=24)
    missed = session.scalar(
        select(func.count())
        .select_from(InstallationAppointment)
        .where(
            InstallationAppointment.status == "missed",
            InstallationAppointment.updated_at >= cutoff,
        )
    ) or 0
    MISSED_APPOINTMENTS_24H.set(missed)

    rows = session.execute(
        select(Subscription.status, func.count()).group_by(Subscription.status)
    ).all()
    counts_by_status = {status: count for status, count in rows}
    for status in (
        "registered", "awaiting_payment", "payment_received", "provisioning", "provisioned",
        "installation_scheduled", "active", "suspended", "cancelled",
    ):
        SUBSCRIPTIONS_TOTAL.labels(status=status).set(counts_by_status.get(status, 0))

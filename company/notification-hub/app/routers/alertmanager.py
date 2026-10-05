from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from shared.clock import utcnow

from app.db import get_session
from app.metrics_gauges import record_message
from app.models import Channel, Message

router = APIRouter(prefix="/api/v1", tags=["alertmanager"])

# department (ticketing/core-api label) -> notification-hub channel slug.
DEPARTMENT_CHANNEL_MAP: dict[str, str] = {
    "TECHNICAL_INFRA": "teknik-altyapi",
    "BILLING": "faturalama",
    "SUBSCRIPTION_OPS": "abonelik-islemleri",
    "FIELD_INSTALL": "saha-kurulum",
}
DEFAULT_CHANNEL = "operasyon-genel"


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "firing"
    labels: dict = {}
    annotations: dict = {}
    startsAt: str | None = None
    endsAt: str | None = None
    fingerprint: str | None = None


class AlertmanagerPayload(BaseModel):
    """Real Alertmanager webhook shape; unknown extra fields (version, groupKey, ...) ignored."""

    model_config = ConfigDict(extra="ignore")

    receiver: str | None = None
    status: str | None = None
    alerts: list[AlertmanagerAlert] = []


# NOTE: no X-API-Key is required on this endpoint. Alertmanager's webhook_configs cannot
# attach custom headers without extra tooling, so this receiver is intentionally
# unauthenticated. It is only reachable inside the compose network, and it never performs a
# mutating company action — it only ever appends an informational channel message.
@router.post("/alertmanager", status_code=202)
def receive_alert(payload: AlertmanagerPayload, session: Session = Depends(get_session)) -> dict:
    posted = 0
    for alert in payload.alerts:
        department = alert.labels.get("department", "")
        channel_slug = DEPARTMENT_CHANNEL_MAP.get(department, DEFAULT_CHANNEL)
        if session.get(Channel, channel_slug) is None:
            channel_slug = DEFAULT_CHANNEL

        alertname = alert.labels.get("alertname", "Uyarı")
        severity = alert.labels.get("severity", "warning")
        summary = alert.annotations.get("summary") or alertname
        description = alert.annotations.get("description", "")

        if alert.status == "resolved":
            title = f"{alertname} çözüldü"
            text = f"Bu uyarı çözüldü: {description or summary}"
            message_severity = "info"
        else:
            title = summary
            text = description or summary
            message_severity = severity if severity in ("info", "warning", "critical") else "warning"

        message = Message(
            channel_slug=channel_slug,
            title=title,
            text=text,
            severity=message_severity,
            source="alertmanager",
            fields=alert.labels,
            external_ref=alert.fingerprint,
            created_at=utcnow(),
        )
        session.add(message)
        session.flush()
        record_message(session, channel_slug, message_severity)
        posted += 1

    return {"received": len(payload.alerts), "posted": posted}

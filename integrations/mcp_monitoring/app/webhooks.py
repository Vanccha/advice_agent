"""`POST /webhooks/alertmanager` — a plain HTTP endpoint, not an MCP tool
(docs/contracts.md §3, §2.7).

Receives the real Alertmanager webhook payload, normalizes each alert to the
shape the assistant expects, and forwards it to `ASSISTANT_ALERT_WEBHOOK_URL`.
The assistant container may not exist yet (or may be down), so a forwarding
failure is logged and counted, never raised — Alertmanager always gets a 2xx
back from this endpoint so it does not spin retrying a webhook that "fails".
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter
from prometheus_client import Counter

from .deps import alert_webhook_url, webhook_forward_client
from .models import AlertmanagerWebhookAlert, AlertmanagerWebhookPayload, NormalizedAlert

logger = logging.getLogger(__name__)

router = APIRouter()

ALERT_FORWARDS_TOTAL = Counter(
    "mcp_monitoring_alert_forwards_total",
    "Alerts received on /webhooks/alertmanager and forwarded to the assistant webhook",
    ["result"],
)


def normalize_alert(alert: AlertmanagerWebhookAlert) -> NormalizedAlert:
    labels = alert.labels or {}
    annotations = alert.annotations or {}
    return NormalizedAlert(
        alertname=str(labels.get("alertname", "unknown")),
        status=alert.status or "firing",
        severity=str(labels.get("severity", "unknown")),
        department=str(labels.get("department", "UNKNOWN")),
        fingerprint=alert.fingerprint or "",
        summary=str(annotations.get("summary", "")),
        description=str(annotations.get("description", "")),
        labels=labels,
        starts_at=alert.startsAt,
    )


async def forward_alert(normalized: NormalizedAlert) -> bool:
    """POST the normalized alert to `ASSISTANT_ALERT_WEBHOOK_URL`. Returns
    whether the forward succeeded; never raises.
    """
    url = alert_webhook_url()
    try:
        response = await webhook_forward_client().post(url, json=normalized.model_dump())
        success = 200 <= response.status_code < 300
        if not success:
            logger.warning(
                "alert forward to %s returned HTTP %s for fingerprint %s",
                url,
                response.status_code,
                normalized.fingerprint,
            )
        return success
    except httpx.HTTPError as exc:
        logger.warning("alert forward to %s failed for fingerprint %s: %s", url, normalized.fingerprint, exc)
        return False
    except Exception as exc:  # noqa: BLE001 - this endpoint must always answer Alertmanager with 2xx
        logger.warning("unexpected error forwarding alert to %s: %s", url, exc)
        return False


@router.post("/webhooks/alertmanager")
async def receive_alertmanager_webhook(payload: AlertmanagerWebhookPayload) -> dict[str, Any]:
    results = []
    for alert in payload.alerts:
        normalized = normalize_alert(alert)
        forwarded = await forward_alert(normalized)
        ALERT_FORWARDS_TOTAL.labels(result="forwarded" if forwarded else "failed").inc()
        results.append({"fingerprint": normalized.fingerprint, "alertname": normalized.alertname, "forwarded": forwarded})
    return {"received": len(payload.alerts), "results": results}

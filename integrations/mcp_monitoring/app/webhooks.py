"""`POST /webhooks/alertmanager`: receive company alerts, normalise and forward them.

Where this sits: called by the company's Alertmanager; calls the assistant's
alert webhook (URL from configuration).

Key ideas:
- Plain HTTP endpoint, not an MCP tool (docs/contracts.md sections 3 and 2.7).
- Never fail the sender: a forwarding failure is logged and counted, and
  Alertmanager always gets a 2xx so it does not retry-storm a webhook.
- Signed webhooks: the forwarded body is signed with HMAC-SHA256 over exactly
  the bytes sent (`X-Webhook-Signature`), so the receiver can verify origin.

Read next: `models.py` for the payload shapes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter
from prometheus_client import Counter

from .deps import alert_webhook_url, settings, webhook_forward_client
from .models import AlertmanagerWebhookAlert, AlertmanagerWebhookPayload, NormalizedAlert

logger = logging.getLogger(__name__)

router = APIRouter()

ALERT_FORWARDS_TOTAL = Counter(
    "mcp_monitoring_alert_forwards_total",
    "Alerts received on /webhooks/alertmanager and forwarded to the assistant webhook",
    ["result"],
)


def normalize_alert(alert: AlertmanagerWebhookAlert) -> NormalizedAlert:
    """Flatten one Alertmanager alert into the shape the receiver expects, with safe defaults for missing labels."""
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
    # Serialise once and sign exactly the bytes that go on the wire.
    body = json.dumps(normalized.model_dump(), separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    secret = settings().WEBHOOK_SECRET
    if secret:
        headers["X-Webhook-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    else:
        logger.warning("WEBHOOK_SECRET is not set; forwarding alert %s unsigned", normalized.fingerprint)
    try:
        response = await webhook_forward_client().post(url, content=body, headers=headers)
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
    """Forward every alert in the batch; always answers 2xx so Alertmanager does not retry."""
    results = []
    for alert in payload.alerts:
        normalized = normalize_alert(alert)
        forwarded = await forward_alert(normalized)
        ALERT_FORWARDS_TOTAL.labels(result="forwarded" if forwarded else "failed").inc()
        results.append({"fingerprint": normalized.fingerprint, "alertname": normalized.alertname, "forwarded": forwarded})
    return {"received": len(payload.alerts), "results": results}

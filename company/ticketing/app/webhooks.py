from __future__ import annotations

import json
import logging
import time

import httpx
from sqlalchemy.orm import Session, sessionmaker

from shared.auth import sign_webhook
from shared.clock import isoformat, utcnow
from app.metrics_gauges import record_webhook_delivery_result
from app.models import Ticket, WebhookDelivery, WebhookSubscription

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0  # overridden to 0 in tests for speed
REQUEST_TIMEOUT_SECONDS = 5.0


def build_webhook_payload(
    *,
    event: str,
    ticket: Ticket,
    old_status: str | None = None,
    new_status: str | None = None,
    comment: str | None = None,
) -> dict:
    """Build the outgoing payload, shape per docs/contracts.md §2.5."""
    return {
        "event": event,
        "ticket_key": ticket.ticket_key,
        "department": ticket.department,
        "old_status": old_status,
        "new_status": new_status if new_status is not None else ticket.status,
        "priority": ticket.priority,
        "assignee": ticket.assignee,
        "external_ref": ticket.external_ref,
        "requester_customer_no": ticket.requester_customer_no,
        "comment": comment,
        "occurred_at": isoformat(utcnow()),
    }


def _deliver_once(target_url: str, secret: str, body: bytes) -> tuple[int | None, str | None]:
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": sign_webhook(secret, body),
    }
    try:
        response = httpx.post(target_url, content=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        return response.status_code, None
    except httpx.HTTPError as exc:
        return None, str(exc)


def deliver_event(session_factory: sessionmaker[Session], ticket_id: int, event: str, payload: dict) -> None:
    """Attempt delivery to every active subscription for `event`, recording every attempt.

    Runs as a FastAPI background task. Must never raise: a failing subscriber must never
    fail the API request that triggered it.
    """
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    session = session_factory()
    try:
        subscriptions = (
            session.query(WebhookSubscription)
            .filter(WebhookSubscription.is_active.is_(True))
            .all()
        )
        for subscription in subscriptions:
            if subscription.events and event not in subscription.events:
                continue
            delivered = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                status_code, error = _deliver_once(subscription.target_url, subscription.secret, body)
                session.add(
                    WebhookDelivery(
                        subscription_id=subscription.id,
                        ticket_id=ticket_id,
                        event=event,
                        payload=payload,
                        attempt=attempt,
                        response_status=status_code,
                        error=error,
                    )
                )
                session.commit()
                if status_code is not None and 200 <= status_code < 300:
                    delivered = True
                    break
                if attempt < MAX_ATTEMPTS and RETRY_DELAY_SECONDS > 0:
                    time.sleep(RETRY_DELAY_SECONDS)
            record_webhook_delivery_result("success" if delivered else "failure")
            if not delivered:
                logger.warning(
                    "webhook delivery to %s failed after %d attempts for event %s",
                    subscription.target_url,
                    MAX_ATTEMPTS,
                    event,
                )
    except Exception:  # noqa: BLE001 - background task must never raise
        logger.exception("unexpected error while delivering webhooks for event %s", event)
    finally:
        session.close()

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from sqlalchemy.orm import sessionmaker

from shared.clock import isoformat, utcnow
from shared.db import session_scope

from app.models import Charge, WebhookDelivery
from app.settings import Settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0


def build_event_payload(event: str, charge: Charge) -> dict[str, Any]:
    return {
        "event": event,
        "charge_ref": charge.charge_ref,
        "status": charge.status,
        "amount_gbp": float(charge.amount_gbp),
        "customer_ref": charge.customer_ref,
        "failure_code": charge.failure_code,
        "occurred_at": isoformat(utcnow()),
    }


def _record_attempt(
    session_factory: sessionmaker,
    *,
    charge_id: int,
    target_url: str,
    event: str,
    attempt: int,
    response_status: int | None,
    error: str | None,
) -> None:
    try:
        with session_scope(session_factory) as session:
            session.add(
                WebhookDelivery(
                    charge_id=charge_id,
                    target_url=target_url,
                    event=event,
                    attempt=attempt,
                    response_status=response_status,
                    error=error,
                )
            )
    except Exception:  # noqa: BLE001 - delivery bookkeeping must never break anything
        logger.exception("failed to record webhook delivery attempt")


def deliver_webhook(
    session_factory: sessionmaker,
    settings: Settings,
    *,
    charge_id: int,
    payload: dict[str, Any],
) -> None:
    """Attempt delivery up to MAX_ATTEMPTS times, RETRY_DELAY_SECONDS apart.

    Designed to run as a FastAPI background task: it must never raise, so the
    request path that scheduled it is never affected by delivery outcomes.
    """
    target_url = settings.core_webhook_url
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": settings.webhook_secret,
    }
    event = payload["event"]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        status_code: int | None = None
        error: str | None = None
        try:
            response = httpx.post(target_url, json=payload, headers=headers, timeout=5.0)
            status_code = response.status_code
            if status_code < 400:
                _record_attempt(
                    session_factory,
                    charge_id=charge_id,
                    target_url=target_url,
                    event=event,
                    attempt=attempt,
                    response_status=status_code,
                    error=None,
                )
                return
            error = f"HTTP {status_code}"
        except Exception as exc:  # noqa: BLE001 - never crash the request path
            error = str(exc)

        _record_attempt(
            session_factory,
            charge_id=charge_id,
            target_url=target_url,
            event=event,
            attempt=attempt,
            response_status=status_code,
            error=error,
        )
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAY_SECONDS)

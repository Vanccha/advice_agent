from __future__ import annotations

import json

import httpx
import respx
from sqlalchemy import select

from app.models import WebhookDelivery
from app.settings import TicketingSettings
from shared.auth import verify_webhook_signature
from shared.db import make_engine, make_session_factory


def _create_ticket(client, auth_headers, external_ref: str) -> dict:
    payload = {
        "department": "TECHNICAL_INFRA",
        "issue_type": "infrastructure_repair",
        "priority": "URGENT",
        "subject": "webhook test",
        "body": "webhook test body",
        "source": "monitoring",
        "external_ref": external_ref,
    }
    resp = client.post("/api/v1/tickets", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


@respx.mock
def test_webhook_payload_shape_and_signature_verifies(client, auth_headers, settings) -> None:
    target_url = "http://webhook-target.invalid/hook"
    route = respx.post(target_url).mock(return_value=httpx.Response(200, json={"ok": True}))

    sub_resp = client.post(
        "/api/v1/webhook-subscriptions",
        json={"name": "test-sub", "target_url": target_url, "events": ["ticket.created"]},
        headers=auth_headers,
    )
    assert sub_resp.status_code == 200

    ticket = _create_ticket(client, auth_headers, "webhook-sig-test")

    assert route.called
    sent_request = route.calls[0].request
    body = sent_request.content
    signature = sent_request.headers["X-Webhook-Signature"]
    assert verify_webhook_signature(settings.webhook_secret, body, signature)

    payload = json.loads(body)
    assert payload["event"] == "ticket.created"
    assert payload["ticket_key"] == ticket["ticket_key"]
    assert payload["department"] == ticket["department"]
    assert payload["new_status"] == "NEW"
    assert payload["old_status"] is None
    assert payload["priority"] == ticket["priority"]
    assert payload["external_ref"] == ticket["external_ref"]
    assert payload["requester_customer_no"] == ticket["requester_customer_no"]
    assert "occurred_at" in payload

    # Recorded as a successful delivery attempt.
    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        deliveries = session.execute(
            select(WebhookDelivery).where(WebhookDelivery.event == "ticket.created")
        ).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].response_status == 200
        assert deliveries[0].error is None
    finally:
        session.close()
        engine.dispose()


@respx.mock
def test_failing_subscriber_does_not_fail_ticket_creation(client, auth_headers, settings) -> None:
    target_url = "http://webhook-target-down.invalid/hook"
    respx.post(target_url).mock(return_value=httpx.Response(500))

    sub_resp = client.post(
        "/api/v1/webhook-subscriptions",
        json={"name": "failing-sub", "target_url": target_url, "events": ["ticket.created"]},
        headers=auth_headers,
    )
    assert sub_resp.status_code == 200

    resp_status = _create_ticket(client, auth_headers, "webhook-failure-test")
    assert resp_status["ticket_key"].startswith("TKT-")  # ticket creation itself succeeded (2xx)

    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        deliveries = session.execute(
            select(WebhookDelivery).where(WebhookDelivery.event == "ticket.created")
        ).scalars().all()
        # 3 attempts, all recorded, all failed (HTTP 500)
        assert len(deliveries) == 3
        assert all(d.response_status == 500 for d in deliveries)
        attempts = sorted(d.attempt for d in deliveries)
        assert attempts == [1, 2, 3]
    finally:
        session.close()
        engine.dispose()

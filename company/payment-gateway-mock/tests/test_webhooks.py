from __future__ import annotations

import httpx
import respx
from sqlalchemy import text

from shared.db import session_scope

from app import webhooks


def test_webhook_delivery_success_recorded(client, auth_headers, settings, session_factory, monkeypatch):
    monkeypatch.setattr(webhooks, "RETRY_DELAY_SECONDS", 0)
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )

    with respx.mock:
        route = respx.post(settings.core_webhook_url).mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        resp = client.post(
            "/psp/v1/charges",
            json={
                "amount_try": 150.0,
                "currency": "TRY",
                "customer_ref": "NH-100002",
                "method": "card",
                "card_token": "tok_test_2",
                "idempotency_key": "wh-success-1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert route.called
        assert route.calls.last.request.headers["X-Webhook-Secret"] == settings.webhook_secret

    with session_scope(session_factory) as session:
        rows = session.execute(
            text(
                "SELECT event, attempt, response_status FROM psp.webhook_deliveries ORDER BY id"
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == "charge.updated"
    assert rows[0][2] == 200


def test_webhook_delivery_retries_and_records_each_attempt(
    client, auth_headers, settings, session_factory, monkeypatch
):
    monkeypatch.setattr(webhooks, "RETRY_DELAY_SECONDS", 0)
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )

    with respx.mock:
        route = respx.post(settings.core_webhook_url).mock(return_value=httpx.Response(500))
        resp = client.post(
            "/psp/v1/charges",
            json={
                "amount_try": 75.0,
                "currency": "TRY",
                "customer_ref": "NH-100003",
                "method": "card",
                "card_token": "tok_test_3",
                "idempotency_key": "wh-fail-1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert route.call_count == 3

    with session_scope(session_factory) as session:
        rows = session.execute(
            text(
                "SELECT attempt, response_status, error FROM psp.webhook_deliveries ORDER BY attempt"
            )
        ).all()
    assert [r[0] for r in rows] == [1, 2, 3]
    assert all(r[1] == 500 for r in rows)


def test_refund_also_triggers_webhook(client, auth_headers, settings, session_factory, monkeypatch):
    monkeypatch.setattr(webhooks, "RETRY_DELAY_SECONDS", 0)
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )

    with respx.mock:
        respx.post(settings.core_webhook_url).mock(return_value=httpx.Response(200))
        created = client.post(
            "/psp/v1/charges",
            json={
                "amount_try": 60.0,
                "currency": "TRY",
                "customer_ref": "NH-100004",
                "method": "card",
                "card_token": "tok_test_4",
                "idempotency_key": "wh-refund-1",
            },
            headers=auth_headers,
        ).json()
        refund_resp = client.post(
            f"/psp/v1/charges/{created['charge_ref']}/refunds",
            json={"amount_try": 60.0},
            headers=auth_headers,
        )
        assert refund_resp.status_code == 201

    with session_scope(session_factory) as session:
        events = session.execute(
            text("SELECT event FROM psp.webhook_deliveries ORDER BY id")
        ).scalars().all()
    assert "charge.updated" in events
    assert "refund.completed" in events

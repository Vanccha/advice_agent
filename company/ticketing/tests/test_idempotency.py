from __future__ import annotations

from sqlalchemy import select

from app.models import Ticket


def _payload(external_ref: str) -> dict:
    return {
        "department": "BILLING",
        "issue_type": "double_charge",
        "priority": "HIGH",
        "subject": "Çift tahsilat",
        "body": "test govde",
        "source": "api",
        "external_ref": external_ref,
    }


def test_duplicate_external_ref_returns_existing_ticket(client, auth_headers, settings) -> None:
    payload = _payload("conv-9f2c1a:double_charge")

    first = client.post("/api/v1/tickets", json=payload, headers=auth_headers)
    assert first.status_code == 201
    first_body = first.json()

    second = client.post("/api/v1/tickets", json=payload, headers=auth_headers)
    assert second.status_code == 200
    second_body = second.json()

    assert second_body["ticket_key"] == first_body["ticket_key"]
    assert second_body["id"] == first_body["id"]

    # Exactly one row must exist for this external_ref.
    from shared.db import make_engine, make_session_factory

    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        rows = session.execute(
            select(Ticket).where(Ticket.external_ref == payload["external_ref"])
        ).scalars().all()
        assert len(rows) == 1
    finally:
        session.close()
        engine.dispose()


def test_different_external_ref_creates_separate_tickets(client, auth_headers) -> None:
    first = client.post("/api/v1/tickets", json=_payload("ref-a"), headers=auth_headers)
    second = client.post("/api/v1/tickets", json=_payload("ref-b"), headers=auth_headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["ticket_key"] != second.json()["ticket_key"]

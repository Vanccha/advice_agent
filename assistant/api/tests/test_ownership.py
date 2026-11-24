"""Identity comes only from the login cookie; conversations and approvals are owner-bound."""
from __future__ import annotations

from api.tests.conftest import login_as

INCIDENT = {
    "incident_no": "INC-2026-030", "region_code": "LDN-CAM", "severity": "critical",
    "status": "open", "title": "Regional outage",
}
OUTAGE_MESSAGE = "My internet is not working, I think there is a problem in the area"


def _bound_customer(conversation_id: str):
    import api.main as main_module
    from core_common.db import session_scope
    from core_common.models import Conversation

    with session_scope(main_module.state.session_factory) as session:
        row = session.query(Conversation).filter(Conversation.conversation_id == conversation_id).one()
        return row.customer_no


def test_body_customer_no_is_ignored(client) -> None:
    login_as(client, "NS-100001")
    turn = client.post("/api/chat", json={"message": "Hello", "customer_no": "NS-100999"}).json()
    assert _bound_customer(turn["conversation_id"]) == "NS-100001"


def test_body_customer_no_without_cookie_does_not_authenticate(client) -> None:
    turn = client.post("/api/chat", json={"message": "Hello", "customer_no": "NS-100999"}).json()
    assert _bound_customer(turn["conversation_id"]) is None


def test_foreign_conversation_is_forbidden(client) -> None:
    login_as(client, "NS-100001")
    conv_id = client.post("/api/chat", json={"message": "Hello"}).json()["conversation_id"]

    login_as(client, "NS-100002")
    response = client.post("/api/chat", json={"message": "Hello", "conversation_id": conv_id})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    client.cookies.clear()
    assert client.post("/api/chat", json={"message": "Hello", "conversation_id": conv_id}).status_code == 403
    assert client.get(f"/api/chat/stream?conversation_id={conv_id}&message=Hello").status_code == 403


def test_own_conversation_can_be_continued(client) -> None:
    login_as(client, "NS-100001")
    conv_id = client.post("/api/chat", json={"message": "Hello"}).json()["conversation_id"]
    again = client.post("/api/chat", json={"message": "Hello", "conversation_id": conv_id})
    assert again.status_code == 200


def test_anonymous_conversation_can_be_continued_and_then_binds(client) -> None:
    conv_id = client.post("/api/chat", json={"message": "Hello"}).json()["conversation_id"]
    login_as(client, "NS-100001")
    assert client.post("/api/chat", json={"message": "Hello", "conversation_id": conv_id}).status_code == 200
    login_as(client, "NS-100002")
    assert client.post("/api/chat", json={"message": "Hello", "conversation_id": conv_id}).status_code == 403


def _pending_approval(client, fake_gateway) -> str:
    fake_gateway.set_response("get_active_incidents_for_region", {"items": [INCIDENT]})
    login_as(client, "NS-100001")
    turn = client.post("/api/chat", json={"message": OUTAGE_MESSAGE}).json()
    assert turn["requires_approval"] is True
    return turn["approval_id"]


def test_approval_requires_owner(client, fake_gateway) -> None:
    approval_id = _pending_approval(client, fake_gateway)

    login_as(client, "NS-100002")
    assert client.post(f"/api/approvals/{approval_id}", json={"decision": "granted"}).status_code == 404
    client.cookies.clear()
    assert client.post(f"/api/approvals/{approval_id}", json={"decision": "granted"}).status_code == 404
    assert not any(name == "apply_outage_credit" for name, _ in fake_gateway.calls_made)

    login_as(client, "NS-100001")
    granted = client.post(f"/api/approvals/{approval_id}", json={"decision": "granted"})
    assert granted.status_code == 200
    assert any(name == "apply_outage_credit" for name, _ in fake_gateway.calls_made)


def test_audit_requires_owner(client) -> None:
    login_as(client, "NS-100001")
    conv_id = client.post("/api/chat", json={"message": "Hello"}).json()["conversation_id"]
    assert client.get(f"/api/conversations/{conv_id}/audit").status_code == 200

    login_as(client, "NS-100002")
    assert client.get(f"/api/conversations/{conv_id}/audit").status_code == 403
    client.cookies.clear()
    assert client.get(f"/api/conversations/{conv_id}/audit").status_code == 403

from __future__ import annotations


def test_approval_flow_for_outage_credit(client, fake_gateway) -> None:
    incident = {
        "incident_no": "INC-2026-030", "region_code": "LDN-CAM", "severity": "critical",
        "status": "open", "title": "Regional outage",
    }
    fake_gateway.set_response("get_active_incidents_for_region", {"items": [incident]})

    turn = client.post(
        "/api/chat",
        json={"message": "My internet is not working, I think there is a problem in the area", "customer_no": "NS-100001"},
    ).json()
    assert turn["mode"] == "AWAITING_APPROVAL"
    assert turn["requires_approval"] is True
    approval_id = turn["approval_id"]
    assert approval_id

    granted = client.post(f"/api/approvals/{approval_id}", json={"decision": "granted"}).json()
    assert granted["mode"] == "CLOSING"
    assert any(name == "apply_outage_credit" for name, _ in fake_gateway.calls_made)


def test_approval_unknown_id_returns_404(client) -> None:
    response = client.post("/api/approvals/999999", json={"decision": "granted"})
    assert response.status_code == 404


def test_approval_invalid_decision_returns_400(client) -> None:
    response = client.post("/api/approvals/1", json={"decision": "maybe"})
    assert response.status_code == 400


def test_audit_trail_endpoint_returns_steps(client) -> None:
    turn = client.post("/api/chat", json={"message": "Hello", "customer_no": "NS-100001"}).json()
    response = client.get(f"/api/conversations/{turn['conversation_id']}/audit")
    assert response.status_code == 200
    steps = response.json()
    assert isinstance(steps, list)
    assert any("step_type" in step for step in steps)

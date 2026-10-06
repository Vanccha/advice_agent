from __future__ import annotations


def _alert(
    status="firing",
    alertname="HighPaymentFailureRate",
    severity="critical",
    department="BILLING",
    summary="High payment failure rate",
    description="More than half of payments failed in the last five minutes.",
    fingerprint="fp-1",
):
    return {
        "status": status,
        "labels": {"alertname": alertname, "severity": severity, "department": department},
        "annotations": {"summary": summary, "description": description},
        "startsAt": "2026-10-05T12:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": fingerprint,
    }


def test_billing_department_fans_into_faturalama(client, api_headers):
    payload = {"receiver": "netswift-channels", "status": "firing", "alerts": [_alert(fingerprint="fp-billing-1")]}
    resp = client.post("/api/v1/alertmanager", json=payload)
    assert resp.status_code == 202
    assert resp.json()["posted"] == 1

    listing = client.get("/api/v1/channels/billing/messages", headers=api_headers)
    items = listing.json()["items"]
    match = next((m for m in items if m["external_ref"] == "fp-billing-1"), None)
    assert match is not None
    assert match["source"] == "alertmanager"
    assert match["severity"] == "critical"


def test_unknown_department_falls_back_to_operasyon_genel(client, api_headers):
    payload = {
        "receiver": "netswift-channels",
        "status": "firing",
        "alerts": [_alert(department="NOT_A_REAL_DEPARTMENT", fingerprint="fp-unknown-1")],
    }
    resp = client.post("/api/v1/alertmanager", json=payload)
    assert resp.status_code == 202

    listing = client.get("/api/v1/channels/ops-general/messages", headers=api_headers)
    items = listing.json()["items"]
    assert any(m["external_ref"] == "fp-unknown-1" for m in items)


def test_missing_department_falls_back_to_operasyon_genel(client, api_headers):
    payload = {
        "receiver": "netswift-channels",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "RegionalOutageDetected", "severity": "critical"},
                "annotations": {"summary": "Regional outage", "description": "details"},
                "fingerprint": "fp-no-dept-1",
            }
        ],
    }
    resp = client.post("/api/v1/alertmanager", json=payload)
    assert resp.status_code == 202

    listing = client.get("/api/v1/channels/ops-general/messages", headers=api_headers)
    items = listing.json()["items"]
    assert any(m["external_ref"] == "fp-no-dept-1" for m in items)


def test_resolved_alert_posts_resolution_message(client, api_headers):
    payload = {
        "receiver": "netswift-channels",
        "status": "resolved",
        "alerts": [
            _alert(
                status="resolved",
                alertname="CoreApiDown",
                severity="critical",
                department="TECHNICAL_INFRA",
                summary="Core API unreachable",
                description="The core-api service was not responding.",
                fingerprint="fp-resolved-1",
            )
        ],
    }
    resp = client.post("/api/v1/alertmanager", json=payload)
    assert resp.status_code == 202

    listing = client.get("/api/v1/channels/technical-infra/messages", headers=api_headers)
    items = listing.json()["items"]
    match = next((m for m in items if m["external_ref"] == "fp-resolved-1"), None)
    assert match is not None
    assert "resolved" in match["title"]
    assert match["severity"] == "info"

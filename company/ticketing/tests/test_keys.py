from __future__ import annotations

import re

TICKET_KEY_RE = re.compile(r"^TKT-\d{4}-\d{5}$")


def _create(client, auth_headers, ref: str) -> str:
    payload = {
        "department": "SUBSCRIPTION_OPS",
        "issue_type": "other",
        "priority": "LOW",
        "subject": "key test",
        "body": "key test",
        "source": "api",
        "external_ref": ref,
    }
    resp = client.post("/api/v1/tickets", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()["ticket_key"]


def test_ticket_key_format_and_uniqueness_and_increment(client, auth_headers) -> None:
    keys = [_create(client, auth_headers, f"seq-{i}") for i in range(5)]

    for key in keys:
        assert TICKET_KEY_RE.match(key), key

    assert len(set(keys)) == len(keys)

    sequence_numbers = [int(key.rsplit("-", 1)[1]) for key in keys]
    assert sequence_numbers == sorted(sequence_numbers)
    assert sequence_numbers == list(range(sequence_numbers[0], sequence_numbers[0] + 5))

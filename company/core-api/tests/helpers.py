from __future__ import annotations

import itertools
import random

_counter = itertools.count(1)
_rng = random.Random(777)


def make_customer_payload() -> dict:
    n = next(_counter)
    return {
        "full_name": f"Test User {n}",
        "national_id": f"ZZ{n:06d}A",
        "phone": f"+447700900{n % 1000:03d}",
        "email": f"test.user{n}@example-mail.test",
        "address_line": "1 Test Street",
        "district": "Camden",
        "city": "London",
        "region_code": "LDN-CAM",
        "gdpr_consent": True,
    }


def create_customer(client, crm_headers) -> dict:
    resp = client.post("/v1/customers", json=make_customer_payload(), headers=crm_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_subscription(client, crm_headers, customer_no: str, package_code: str = "FIBER_100_BASIC") -> dict:
    resp = client.post(
        "/v1/subscriptions",
        json={"customer_no": customer_no, "package_code": package_code},
        headers=crm_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_customer_with_subscription(client, crm_headers, package_code: str = "FIBER_100_BASIC") -> tuple[dict, dict]:
    customer = create_customer(client, crm_headers)
    subscription = create_subscription(client, crm_headers, customer["customer_no"], package_code)
    return customer, subscription

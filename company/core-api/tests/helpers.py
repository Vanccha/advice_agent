from __future__ import annotations

import itertools
import random

_counter = itertools.count(1)
_rng = random.Random(777)


def make_customer_payload() -> dict:
    n = next(_counter)
    return {
        "full_name": f"Test Kullanici {n}",
        "national_id": f"1{n:010d}"[:11],
        "phone": f"+9053{n:08d}"[:13],
        "email": f"test.kullanici{n}@ornek-eposta.test",
        "address_line": "Test Sokak No:1",
        "district": "Kadıköy",
        "city": "İstanbul",
        "region_code": "IST-KAD",
        "kvkk_consent": True,
    }


def create_customer(client, crm_headers) -> dict:
    resp = client.post("/v1/customers", json=make_customer_payload(), headers=crm_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_subscription(client, crm_headers, customer_no: str, package_code: str = "FIBER_100_TEMEL") -> dict:
    resp = client.post(
        "/v1/subscriptions",
        json={"customer_no": customer_no, "package_code": package_code},
        headers=crm_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_customer_with_subscription(client, crm_headers, package_code: str = "FIBER_100_TEMEL") -> tuple[dict, dict]:
    customer = create_customer(client, crm_headers)
    subscription = create_subscription(client, crm_headers, customer["customer_no"], package_code)
    return customer, subscription

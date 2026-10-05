"""Live checks against the real payment-gateway + company-db. Skips cleanly
when the stack isn't reachable. `detect_duplicate_charges` is exercised
end-to-end by planting two same-amount `succeeded` charges for a synthetic,
test-only `customer_ref` directly through the PSP's own public REST API
(never SQL) — this never touches real seeded demo customers and never
flips the shared gateway's outage/control flags.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.deps import payment_api_client, settings
from app.models import (
    DetectDuplicateChargesInput,
    GetPaymentStatusInput,
    ListCustomerChargesInput,
)
from app.tools import (
    handle_detect_duplicate_charges,
    handle_get_payment_status,
    handle_list_customer_charges,
)


def _payment_gateway_reachable() -> bool:
    try:
        httpx.get(f"{settings().PAYMENT_API_BASE_URL}/health", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_if_unreachable() -> None:
    if not _payment_gateway_reachable():
        pytest.skip("payment-gateway not reachable from this environment")


async def _plant_charge(customer_ref: str, amount_try: float) -> None:
    payload = {
        "amount_try": amount_try,
        "currency": "TRY",
        "customer_ref": customer_ref,
        "method": "card",
        "idempotency_key": f"mcp-payment-test-{uuid.uuid4()}",
    }
    result = await payment_api_client().post("/psp/v1/charges", "payment_api", json=payload)
    assert result.ok, result.error


async def test_detect_duplicate_charges_finds_a_live_planted_pair() -> None:
    customer_ref = f"TESTDUP-{uuid.uuid4().hex[:8]}"
    await _plant_charge(customer_ref, 459.00)
    await _plant_charge(customer_ref, 459.00)

    result = await handle_detect_duplicate_charges(
        DetectDuplicateChargesInput(customer_no=customer_ref, window_minutes=60)
    )
    assert result.ok is True
    assert len(result.data.duplicate_groups) == 1
    assert result.data.duplicate_groups[0].amount_try == 459.00
    assert result.data.duplicate_groups[0].count == 2


async def test_detect_duplicate_charges_ignores_different_amounts_live() -> None:
    customer_ref = f"TESTDUP-{uuid.uuid4().hex[:8]}"
    await _plant_charge(customer_ref, 100.00)
    await _plant_charge(customer_ref, 200.00)

    result = await handle_detect_duplicate_charges(
        DetectDuplicateChargesInput(customer_no=customer_ref, window_minutes=60)
    )
    assert result.ok is True
    assert result.data.duplicate_groups == []


async def test_list_customer_charges_sees_the_planted_charge() -> None:
    customer_ref = f"TESTLIST-{uuid.uuid4().hex[:8]}"
    await _plant_charge(customer_ref, 77.00)

    result = await handle_list_customer_charges(ListCustomerChargesInput(customer_no=customer_ref))
    assert result.ok is True
    assert result.source == "payment_api"
    assert len(result.data.charges) == 1
    assert result.data.charges[0].amount_try == 77.00


async def test_get_payment_status_for_seeded_customer() -> None:
    import os

    if not os.environ.get("DIAG_DATABASE_URL"):
        pytest.skip("DIAG_DATABASE_URL not set")
    result = await handle_get_payment_status(GetPaymentStatusInput(customer_no="NH-100001"))
    if not result.ok:
        pytest.skip(f"diag_db not reachable: {result.error}")
    assert len(result.data.payments) >= 1
    assert result.data.payments[0].customer_no == "NH-100001"


async def test_get_payment_status_without_any_filter_is_invalid_input() -> None:
    result = await handle_get_payment_status(GetPaymentStatusInput())
    assert result.ok is False
    assert result.error.code == "INVALID_INPUT"

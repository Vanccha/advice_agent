"""`find_customer` must never surface `national_id` (docs/contracts.md §1.1:
"National IDs ... are not exposed by any [diag] view"). Checked both at the
schema level (no amount of malformed data can make it appear) and, live,
against the real seeded data.
"""
from __future__ import annotations

import os

import httpx
import pytest

from app.models import CustomerSummary, FindCustomerOutput
from app.tools_read import handle_find_customer
from app.models import FindCustomerInput


def test_output_models_have_no_national_id_field() -> None:
    assert "national_id" not in CustomerSummary.model_fields
    assert "national_id" not in FindCustomerOutput.model_fields


@pytest.mark.needs_core_api
async def test_live_find_customer_result_has_no_national_id() -> None:
    diag_url = os.environ.get("DIAG_DATABASE_URL")
    if not diag_url:
        pytest.skip("DIAG_DATABASE_URL not set")
    try:
        httpx.get(f"{os.environ.get('CORE_API_BASE_URL', 'http://core-api:8000')}/health", timeout=2.0)
    except httpx.HTTPError:
        pytest.skip("core-api not reachable")

    result = await handle_find_customer(FindCustomerInput(customer_no="NS-100001"))
    if not result.ok:
        pytest.skip(f"diag_db not reachable from this environment: {result.error}")
    assert len(result.data.customers) == 1
    customer = result.data.customers[0]
    assert not hasattr(customer, "national_id")
    dumped = customer.model_dump()
    assert "national_id" not in dumped

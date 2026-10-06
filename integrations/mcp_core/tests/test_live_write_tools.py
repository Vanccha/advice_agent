"""Live checks of the mutating tools that *are* in scope for
`partner-integration` (credits, provisioning retry, notification resend).
Skips cleanly when the stack isn't reachable.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.deps import settings
from app.models import ApplyOutageCreditInput
from app.tools_write import handle_apply_outage_credit


def _core_api_reachable() -> bool:
    try:
        httpx.get(f"{settings().CORE_API_BASE_URL}/health", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_if_unreachable() -> None:
    if not _core_api_reachable():
        pytest.skip("core-api not reachable from this environment")


async def test_apply_outage_credit_over_cap_is_credit_limit_exceeded() -> None:
    result = await handle_apply_outage_credit(
        ApplyOutageCreditInput(
            subscription_id=2,
            amount_gbp=999.0,
            reason="integration test: over cap",
            idempotency_key=f"test-{uuid.uuid4()}",
        )
    )
    assert result.ok is False
    assert result.error.code == "CREDIT_LIMIT_EXCEEDED"


async def test_apply_outage_credit_within_cap_succeeds_and_generates_idempotency_key() -> None:
    result = await handle_apply_outage_credit(
        ApplyOutageCreditInput(subscription_id=2, amount_gbp=10.0, reason="integration test: small credit")
    )
    assert result.ok is True
    assert result.data.amount_gbp == 10.0
    assert result.data.idempotency_key  # auto-generated since none was given
    assert result.data.created_by == "partner-integration"

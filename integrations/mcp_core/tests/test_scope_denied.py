"""`partner-integration` deliberately lacks `billing:refund` and
`appointments:write` (docs/contracts.md §2.1). `request_refund` exists
precisely to prove that boundary holds end to end: calling it must come back
`ToolResult.fail("SCOPE_DENIED", ...)`, never a raised exception and never a
silent success.
"""
from __future__ import annotations

import httpx
import pytest

from app.deps import settings
from app.models import RequestRefundInput, RescheduleInstallationInput
from app.tools_write import handle_request_refund, handle_reschedule_installation


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


async def test_request_refund_is_always_scope_denied() -> None:
    result = await handle_request_refund(RequestRefundInput(payment_id=1, amount_try=1.0, reason="test"))
    assert result.ok is False
    assert result.error.code == "SCOPE_DENIED"
    assert result.source == "core_api"
    assert result.data is None


async def test_reschedule_installation_is_always_scope_denied() -> None:
    result = await handle_reschedule_installation(
        RescheduleInstallationInput(appointment_id=1, scheduled_date="2026-11-01", time_slot="09-12")
    )
    assert result.ok is False
    assert result.error.code == "SCOPE_DENIED"
    assert result.source == "core_api"

"""`post_department_message` must never raise even when notification-hub is
unreachable, and must preserve the company's own error code for an unknown
channel. Mocked so these never depend on live service state.
"""
from __future__ import annotations

import httpx
import respx

from app.deps import settings
from app.models import PostDepartmentMessageInput
from app.tools import handle_post_department_message


async def test_post_department_message_maps_channel_not_found() -> None:
    base_url = settings().NOTIFICATION_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.post("/api/v1/channels/does-not-exist/messages").mock(
            return_value=httpx.Response(
                404,
                json={
                    "error": {
                        "code": "CHANNEL_NOT_FOUND",
                        "message": "Channel 'does-not-exist' was not found.",
                        "details": {"slug": "does-not-exist"},
                    }
                },
            )
        )
        result = await handle_post_department_message(
            PostDepartmentMessageInput(channel="does-not-exist", title="t", text="x")
        )

    assert result.ok is False
    assert result.error.code == "CHANNEL_NOT_FOUND"


async def test_post_department_message_never_raises_when_hub_is_down() -> None:
    base_url = settings().NOTIFICATION_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.post("/api/v1/channels/billing/messages").mock(side_effect=httpx.ConnectError("refused"))
        result = await handle_post_department_message(
            PostDepartmentMessageInput(channel="billing", title="t", text="x")
        )

    assert result.ok is False
    assert result.error.code == "UPSTREAM_UNAVAILABLE"


async def test_post_department_message_forwards_fields_and_external_ref() -> None:
    base_url = settings().NOTIFICATION_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        route = mock.post("/api/v1/channels/billing/messages").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": 1,
                    "channel_slug": "billing",
                    "title": "t",
                    "text": "x",
                    "severity": "warning",
                    "source": "mcp-notification",
                    "fields": {"amount_gbp": 459.0},
                    "external_ref": "conv-1:double_charge",
                    "created_at": "2026-10-05T12:00:00Z",
                },
            )
        )
        result = await handle_post_department_message(
            PostDepartmentMessageInput(
                channel="billing",
                title="t",
                text="x",
                severity="warning",
                fields={"amount_gbp": 459.0},
                external_ref="conv-1:double_charge",
            )
        )

    assert result.ok is True
    sent_body = route.calls.last.request.content
    import json as _json

    sent = _json.loads(sent_body)
    assert sent["fields"] == {"amount_gbp": 459.0}
    assert sent["external_ref"] == "conv-1:double_charge"
    assert sent["severity"] == "warning"
    assert result.data.message.fields == {"amount_gbp": 459.0}

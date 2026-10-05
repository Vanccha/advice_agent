"""Live checks against the real notification-hub. Skips cleanly when the
stack isn't reachable from the current environment. Every message posted
here uses a distinctive title so it's easy to spot in the channel UI.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.deps import settings
from app.models import ListChannelMessagesInput, PostDepartmentMessageInput
from app.tools import handle_list_channel_messages, handle_post_department_message


def _notification_hub_reachable() -> bool:
    try:
        httpx.get(f"{settings().NOTIFICATION_API_BASE_URL}/health", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_if_unreachable() -> None:
    if not _notification_hub_reachable():
        pytest.skip("notification-hub not reachable from this environment")


async def test_post_department_message_lands_and_is_read_back() -> None:
    marker = f"adapter-check-{uuid.uuid4()}"
    posted = await handle_post_department_message(
        PostDepartmentMessageInput(
            channel="operasyon-genel",
            title=marker,
            text="mcp-notification adapter check",
            severity="info",
            fields={"check": "mcp-notification"},
            external_ref=marker,
        )
    )
    assert posted.ok, posted.error
    assert posted.source == "notification_api"
    assert posted.data.message.title == marker
    assert posted.data.message.channel_slug == "operasyon-genel"
    assert posted.data.message.external_ref == marker

    listed = await handle_list_channel_messages(ListChannelMessagesInput(channel="operasyon-genel", limit=50))
    assert listed.ok, listed.error
    assert any(m.title == marker for m in listed.data.messages)


async def test_post_department_message_unknown_channel_is_clean_failure() -> None:
    result = await handle_post_department_message(
        PostDepartmentMessageInput(channel="does-not-exist", title="x", text="y")
    )
    assert result.ok is False
    assert result.error.code == "CHANNEL_NOT_FOUND"


async def test_list_channel_messages_unknown_channel_is_clean_failure() -> None:
    result = await handle_list_channel_messages(ListChannelMessagesInput(channel="does-not-exist"))
    assert result.ok is False
    assert result.error.code == "CHANNEL_NOT_FOUND"

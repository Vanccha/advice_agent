"""`mcp-notification` tools (docs/contracts.md §3)."""
from __future__ import annotations

from typing import Any

from common.result import ToolResult, ok
from common.tool_spec import register_tool

from .deps import notification_api_client
from .models import (
    ListChannelMessagesInput,
    ListChannelMessagesOutput,
    MessageDetail,
    PostDepartmentMessageInput,
    PostDepartmentMessageOutput,
)

SOURCE_NOTIFICATION = "notification_api"

# Identifies this adapter as the sender in notification-hub's own `source`
# field (docs/contracts.md §1.4: "source: free text"). An unknown channel
# comes back from notification-hub as a clean `CHANNEL_NOT_FOUND` error,
# which `CompanyApiClient.post`/`.get` already map verbatim into
# `ToolResult.fail(...)` — no special-casing needed here.
_SOURCE_LABEL = "mcp-notification"


async def handle_post_department_message(inp: PostDepartmentMessageInput) -> ToolResult[Any]:
    payload: dict[str, Any] = {
        "title": inp.title,
        "text": inp.text,
        "severity": inp.severity,
        "source": _SOURCE_LABEL,
        "fields": inp.fields or {},
    }
    if inp.external_ref is not None:
        payload["external_ref"] = inp.external_ref

    result = await notification_api_client().post(
        f"/api/v1/channels/{inp.channel}/messages", SOURCE_NOTIFICATION, json=payload
    )
    if not result.ok:
        return result
    return ok(PostDepartmentMessageOutput(message=MessageDetail(**result.data)), SOURCE_NOTIFICATION)


async def handle_list_channel_messages(inp: ListChannelMessagesInput) -> ToolResult[Any]:
    result = await notification_api_client().get(
        f"/api/v1/channels/{inp.channel}/messages", SOURCE_NOTIFICATION, params={"limit": inp.limit}
    )
    if not result.ok:
        return result
    body = result.data or {}
    messages = [MessageDetail(**item) for item in body.get("items", [])]
    return ok(
        ListChannelMessagesOutput(messages=messages, total=body.get("total", len(messages))),
        SOURCE_NOTIFICATION,
    )


def register_tools(mcp: Any) -> None:
    register_tool(
        mcp,
        name="post_department_message",
        description="Post a message to a department notification channel.",
        input_model=PostDepartmentMessageInput,
        output_model=PostDepartmentMessageOutput,
        handler=handle_post_department_message,
    )
    register_tool(
        mcp,
        name="list_channel_messages",
        description="List the most recent messages posted to a department notification channel.",
        input_model=ListChannelMessagesInput,
        output_model=ListChannelMessagesOutput,
        handler=handle_list_channel_messages,
    )

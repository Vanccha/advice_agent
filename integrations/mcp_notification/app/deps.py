"""Process-wide dependencies for the `mcp-notification` server: settings and
the one `CompanyApiClient` pointed at notification-hub with `NOTIFY_API_KEY`
(falling back to `COMPANY_API_KEY` for consistency with the other adapters).
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Optional

from common.http import CompanyApiClient
from common.settings import AdapterSettings, get_settings


@lru_cache
def settings() -> AdapterSettings:
    return get_settings()


_notification_api_client: Optional[CompanyApiClient] = None
_notification_api_client_loop: Optional[asyncio.AbstractEventLoop] = None


def notification_api_client() -> CompanyApiClient:
    """Return the process-wide `CompanyApiClient` for notification-hub,
    recreating it if the running event loop changed (see mcp_core's
    `deps.core_api_client` for why).
    """
    global _notification_api_client, _notification_api_client_loop
    loop = asyncio.get_event_loop()
    if _notification_api_client is None or _notification_api_client_loop is not loop:
        s = settings()
        api_key = s.NOTIFY_API_KEY or s.COMPANY_API_KEY
        _notification_api_client = CompanyApiClient(s.NOTIFICATION_API_BASE_URL, api_key)
        _notification_api_client_loop = loop
    return _notification_api_client

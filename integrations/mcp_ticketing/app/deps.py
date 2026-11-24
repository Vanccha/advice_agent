"""Process-wide dependencies for the `mcp-ticketing` server: settings and the one `CompanyApiClient` pointed at the ticketing service.

Where this sits: imported by this adapter's tools; builds clients lazily so
importing the module never touches the network.

Key ideas:
- Dependency access through small functions (`settings()`, a client getter)
  keeps tools easy to test: tests monkeypatch the getter, not global state.
- `TICKETING_API_KEY` is used when set and falls back to `COMPANY_API_KEY`, so least-privilege keys can be introduced without code changes.
- httpx's `AsyncClient` is bound to the event loop it was created on, so the
  getter rebuilds the client when the running loop changes (matters for test
  suites that create a loop per test; uvicorn has exactly one).

Read next: `tools.py` in this package.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Optional

from common.http import CompanyApiClient
from common.settings import AdapterSettings, get_settings


@lru_cache
def settings() -> AdapterSettings:
    """Cached adapter settings."""
    return get_settings()


_ticketing_api_client: Optional[CompanyApiClient] = None
_ticketing_api_client_loop: Optional[asyncio.AbstractEventLoop] = None


def ticketing_api_client() -> CompanyApiClient:
    """Return the process-wide `CompanyApiClient` for the ticketing service,
    recreating it if the running event loop changed (see mcp_core's
    `deps.core_api_client` for why).
    """
    global _ticketing_api_client, _ticketing_api_client_loop
    loop = asyncio.get_event_loop()
    if _ticketing_api_client is None or _ticketing_api_client_loop is not loop:
        s = settings()
        api_key = s.TICKETING_API_KEY or s.COMPANY_API_KEY
        _ticketing_api_client = CompanyApiClient(s.TICKETING_API_BASE_URL, api_key)
        _ticketing_api_client_loop = loop
    return _ticketing_api_client

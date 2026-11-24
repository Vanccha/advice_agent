"""Process-wide dependencies for the `mcp-payment` server: settings and the one `CompanyApiClient` pointed at the payment gateway (PSP simulator).

Where this sits: imported by this adapter's tools; builds clients lazily so
importing the module never touches the network.

Key ideas:
- Dependency access through small functions (`settings()`, a client getter)
  keeps tools easy to test: tests monkeypatch the getter, not global state.
- The PSP's charge endpoints do not need the key in this simulator, but its control endpoint does, so the client always carries `PSP_API_KEY`.
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


_payment_api_client: Optional[CompanyApiClient] = None
_payment_api_client_loop: Optional[asyncio.AbstractEventLoop] = None


def payment_api_client() -> CompanyApiClient:
    """Return the process-wide `CompanyApiClient` for the payment gateway,
    recreating it if the running event loop changed (see mcp_core's
    `deps.core_api_client` for why).
    """
    global _payment_api_client, _payment_api_client_loop
    loop = asyncio.get_event_loop()
    if _payment_api_client is None or _payment_api_client_loop is not loop:
        s = settings()
        api_key = s.PSP_API_KEY or s.COMPANY_API_KEY
        _payment_api_client = CompanyApiClient(s.PAYMENT_API_BASE_URL, api_key)
        _payment_api_client_loop = loop
    return _payment_api_client


def diag_database_url() -> str:
    """DSN of the read-only diagnostic database."""
    return settings().DIAG_DATABASE_URL

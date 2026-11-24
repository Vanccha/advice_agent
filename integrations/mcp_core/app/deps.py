"""Process-wide dependencies for the `mcp-core` server: settings and the one `CompanyApiClient` pointed at core-api.

Where this sits: imported by this adapter's tools; builds clients lazily so
importing the module never touches the network.

Key ideas:
- Dependency access through small functions (`settings()`, a client getter)
  keeps tools easy to test: tests monkeypatch the getter, not global state.
- Writes use the `partner-integration` service-account key, so core-api's own scope checks decide what this adapter may do.
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


_core_api_client: Optional[CompanyApiClient] = None
_core_api_client_loop: Optional[asyncio.AbstractEventLoop] = None


def core_api_client() -> CompanyApiClient:
    """Return the process-wide `CompanyApiClient`, recreating it if the running
    event loop changed (httpx's AsyncClient is bound to the loop it was built
    on; under uvicorn there's exactly one loop for the process's lifetime, but
    a test suite that spins a fresh loop per test needs this to not hand back
    a client tied to an already-closed loop).
    """
    global _core_api_client, _core_api_client_loop
    loop = asyncio.get_event_loop()
    if _core_api_client is None or _core_api_client_loop is not loop:
        s = settings()
        _core_api_client = CompanyApiClient(s.CORE_API_BASE_URL, s.COMPANY_API_KEY)
        _core_api_client_loop = loop
    return _core_api_client


def diag_database_url() -> str:
    """DSN of the read-only diagnostic database."""
    return settings().DIAG_DATABASE_URL

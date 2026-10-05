"""Process-wide dependencies for the `mcp-core` server: settings and the one
`CompanyApiClient` pointed at core-api with the `partner-integration` key.
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
    return settings().DIAG_DATABASE_URL

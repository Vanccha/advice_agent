"""Process-wide dependencies for the `mcp-monitoring` server: settings and
plain `httpx.AsyncClient`s for Prometheus and Alertmanager.

`mcp-monitoring` carries no company API key (docs/contracts.md §3: "no
company key — reads Prometheus/Alertmanager"), so, unlike every other
adapter's `deps.py`, these clients are bare `httpx.AsyncClient`s rather than
`common.http.CompanyApiClient` — there's no `X-API-Key` to attach and
Prometheus/Alertmanager's own response shapes (`{"status":"success"|"error",
"data":{...}}`, the Alertmanager v2 alert list) don't match the company
REST error envelope `CompanyApiClient` is built to unpack.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Optional

import httpx

from common.settings import AdapterSettings, get_settings

@lru_cache
def settings() -> AdapterSettings:
    return get_settings()


def alert_webhook_url() -> str:
    """Where a normalized alert is forwarded (deployment wiring, see AdapterSettings)."""
    return settings().ASSISTANT_ALERT_WEBHOOK_URL


_prometheus_client: Optional[httpx.AsyncClient] = None
_prometheus_client_loop: Optional[asyncio.AbstractEventLoop] = None

_alertmanager_client: Optional[httpx.AsyncClient] = None
_alertmanager_client_loop: Optional[asyncio.AbstractEventLoop] = None

_webhook_client: Optional[httpx.AsyncClient] = None
_webhook_client_loop: Optional[asyncio.AbstractEventLoop] = None


def prometheus_client() -> httpx.AsyncClient:
    global _prometheus_client, _prometheus_client_loop
    loop = asyncio.get_event_loop()
    if _prometheus_client is None or _prometheus_client_loop is not loop:
        _prometheus_client = httpx.AsyncClient(base_url=settings().PROMETHEUS_BASE_URL, timeout=10.0)
        _prometheus_client_loop = loop
    return _prometheus_client


def alertmanager_client() -> httpx.AsyncClient:
    global _alertmanager_client, _alertmanager_client_loop
    loop = asyncio.get_event_loop()
    if _alertmanager_client is None or _alertmanager_client_loop is not loop:
        _alertmanager_client = httpx.AsyncClient(base_url=settings().ALERTMANAGER_BASE_URL, timeout=10.0)
        _alertmanager_client_loop = loop
    return _alertmanager_client


def webhook_forward_client() -> httpx.AsyncClient:
    """Client used only to forward normalized alerts to
    `ASSISTANT_ALERT_WEBHOOK_URL`. No base_url: that env var is a full URL,
    and the assistant container may not exist yet, so this must never raise
    out of the caller — see `app.webhooks.forward_alert`.
    """
    global _webhook_client, _webhook_client_loop
    loop = asyncio.get_event_loop()
    if _webhook_client is None or _webhook_client_loop is not loop:
        _webhook_client = httpx.AsyncClient(timeout=5.0)
        _webhook_client_loop = loop
    return _webhook_client

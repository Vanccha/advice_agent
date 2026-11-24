"""The API keys core-api accepts and the scopes each key carries.

Where this sits: read by every router through `require_scope(..., get_registry())`.

Key ideas:
- API-key scopes: the CRM key may do nearly everything (including
  `billing:refund`), while the partner key gets only reads, a provisioning
  retry, credits and ticket writes. A refund attempted with the partner key
  is refused by the company API itself (`SCOPE_DENIED`), not by the caller.
- Least privilege: grant the smallest scope set that lets a client do its job.

Read next: `routers/billing.py`, where the refund scope is enforced.
"""
from __future__ import annotations

from functools import lru_cache

from shared.auth import ApiKeyRegistry

from app.settings import get_settings

CRM_SCOPES = {
    "customers:*", "subscriptions:*", "payments:*", "billing:refund",
    "provisioning:*", "incidents:*", "appointments:*", "notifications:send",
    "credits:write",
}
PARTNER_SCOPES = {
    "customers:read", "subscriptions:read", "payments:read", "provisioning:read",
    "provisioning:retry", "incidents:read", "appointments:read",
    "notifications:resend", "credits:write", "tickets:write",
}


@lru_cache
def get_registry() -> ApiKeyRegistry:
    """Build (once) the key registry from the environment-provided keys."""
    settings = get_settings()
    registry = ApiKeyRegistry()
    registry.register(settings.core_api_key_crm, "netswift-crm", CRM_SCOPES)
    registry.register(settings.core_api_key_partner, "partner-integration", PARTNER_SCOPES)
    return registry

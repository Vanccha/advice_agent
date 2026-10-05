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
    settings = get_settings()
    registry = ApiKeyRegistry()
    registry.register(settings.core_api_key_crm, "nethiz-crm", CRM_SCOPES)
    registry.register(settings.core_api_key_partner, "partner-integration", PARTNER_SCOPES)
    return registry

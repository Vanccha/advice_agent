from __future__ import annotations

import uuid

from shared.errors import ApiError
from shared.http_client import ServiceClient

from chaos.errors import ChaosError
from chaos.settings import ChaosSettings


def payment_control_client(settings: ChaosSettings) -> ServiceClient:
    return ServiceClient(
        base_url=settings.payment_api_base_url,
        api_key=settings.psp_api_key,
        upstream_name="payment-gateway",
    )


def get_control_flags(settings: ChaosSettings) -> dict:
    client = payment_control_client(settings)
    return client.get("/psp/v1/control").json()


def set_control_flags(settings: ChaosSettings, **flags: object) -> dict:
    client = payment_control_client(settings)
    return client.post("/psp/v1/control", json_body=flags).json()


def core_api_client(settings: ChaosSettings) -> ServiceClient:
    return ServiceClient(
        base_url=settings.core_api_base_url,
        api_key=settings.core_api_key_crm,
        upstream_name="core-api",
    )


def pay_subscription_until_succeeded(
    settings: ChaosSettings, subscription_id: int, *, attempts: int = 6
) -> dict:
    """Drive a subscription through the real payments endpoint until it succeeds.

    The PSP mock randomly fails a small fraction of charges; this uses the
    company's own write API (not a fake SQL row) to reach 'payment_received',
    retrying with a fresh idempotency key the way a real client would after a
    failed charge.
    """
    client = core_api_client(settings)
    last_result: dict | None = None
    for _ in range(attempts):
        idem = f"chaos-pay-{subscription_id}-{uuid.uuid4().hex[:10]}"
        try:
            response = client.post(
                f"/v1/subscriptions/{subscription_id}/payments",
                json_body={"method": "card", "idempotency_key": idem},
            )
        except ApiError as exc:
            raise ChaosError(f"core-api rejected payment for subscription {subscription_id}: {exc}") from exc
        last_result = response.json()
        if last_result.get("status") == "succeeded":
            return last_result
    raise ChaosError(
        f"Could not get a succeeded payment for subscription {subscription_id} after "
        f"{attempts} attempts (last status: {last_result.get('status') if last_result else 'unknown'})."
    )

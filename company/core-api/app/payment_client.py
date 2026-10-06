from __future__ import annotations

from typing import Any

import httpx

from shared.errors import UpstreamUnavailable
from shared.http_client import ServiceClient

from app.settings import CoreApiSettings


class PaymentClient:
    """Talks to the payment-gateway-mock (PSP) service."""

    def __init__(self, settings: CoreApiSettings) -> None:
        self._client = ServiceClient(
            base_url=settings.payment_api_base_url,
            api_key=settings.psp_api_key,
            upstream_name="payment-gateway",
        )

    def create_charge(
        self,
        *,
        amount_gbp: float,
        customer_ref: str,
        method: str,
        card_token: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        body = {
            "amount_gbp": float(amount_gbp),
            "currency": "GBP",
            "customer_ref": customer_ref,
            "method": method,
            "card_token": card_token,
            "idempotency_key": idempotency_key,
        }
        try:
            response = self._client.post(
                "/psp/v1/charges", json_body=body, raise_for_status=False
            )
        except httpx.HTTPError as exc:  # pragma: no cover - network edge
            raise UpstreamUnavailable(
                "UPSTREAM_UNAVAILABLE", "payment-gateway is not reachable."
            ) from exc

        if response.status_code == 503:
            raise UpstreamUnavailable(
                "UPSTREAM_UNAVAILABLE", "payment-gateway is temporarily unavailable."
            )
        if response.status_code >= 400:
            payload = {}
            try:
                payload = response.json()
            except ValueError:
                pass
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise UpstreamUnavailable(
                "UPSTREAM_UNAVAILABLE",
                error.get("message", "payment-gateway returned an error."),
            )
        return response.json()

    def refund(self, *, charge_ref: str, amount_gbp: float, reason: str) -> dict[str, Any]:
        try:
            response = self._client.post(
                f"/psp/v1/charges/{charge_ref}/refunds",
                json_body={"amount_gbp": float(amount_gbp), "reason": reason},
                raise_for_status=False,
            )
        except httpx.HTTPError as exc:  # pragma: no cover
            raise UpstreamUnavailable(
                "UPSTREAM_UNAVAILABLE", "payment-gateway is not reachable."
            ) from exc

        if response.status_code == 503:
            raise UpstreamUnavailable(
                "UPSTREAM_UNAVAILABLE", "payment-gateway is temporarily unavailable."
            )
        if response.status_code >= 400:
            payload = {}
            try:
                payload = response.json()
            except ValueError:
                pass
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            from shared.errors import ApiError

            raise ApiError(
                code=error.get("code", "REFUND_FAILED"),
                message=error.get("message", "Refund request failed."),
                status_code=response.status_code,
            )
        return response.json()

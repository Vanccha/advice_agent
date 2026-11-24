"""Scenario F: take the payment gateway down through its control endpoint.

Where this sits: the only scenario that needs no victim record; it flips the
gateway's outage flag.

Key ideas:
- Chaos / failure injection through the system's own switch: the gateway
  already supports a deliberate outage, so nothing is faked.
- Prometheus then fires `PaymentGatewayDown`, which routes to the technical
  infrastructure channel.

Read next: `company/payment-gateway-mock/app/outage.py`.
"""
from __future__ import annotations

from chaos.http import get_control_flags, set_control_flags
from chaos.settings import ChaosSettings
from chaos.scenarios.common import ResetOutcome, ScenarioOutcome


def apply_payment_down(settings: ChaosSettings) -> ScenarioOutcome:
    flags = set_control_flags(settings, outage=True)
    return ScenarioOutcome(
        scenario="payment_down",
        dry_run=False,
        picked={},
        changes=[f"psp.control_flags.outage: -> true (POST /psp/v1/control)"],
        expected_behavior=(
            "Prometheus must fire PaymentGatewayDown (up==0 or psp_outage==1, for 1m) -> "
            "Alertmanager -> the integration webhook. Expected: proactive diagnosis, a "
            "TECHNICAL_INFRA ticket, a department channel message, and users told the "
            "payment system is temporarily unavailable."
        ),
        records={"control_flags": flags},
    )


def detect_payment_down(settings: ChaosSettings) -> dict:
    flags = get_control_flags(settings)
    return flags


def reset_payment_down(settings: ChaosSettings) -> ResetOutcome:
    flags = set_control_flags(
        settings, outage=False, force_failure_code=None, failure_rate=settings.psp_failure_rate
    )
    return ResetOutcome(
        scenario="payment_down",
        changes=[
            f"psp.control_flags: outage=false, force_failure_code=null, "
            f"failure_rate={settings.psp_failure_rate} (POST /psp/v1/control)"
        ],
        records={"control_flags": flags},
    )

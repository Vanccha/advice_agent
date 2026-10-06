"""Reason-code → customer-facing sentence fallback table (contracts §4.6).

Used by `PolicyEngine` whenever `policy.yaml` does not already carry an explicit `reason_en`
for the situation (e.g. a generic condition failure, an unknown action, a rate limit hit).
When the policy file *does* provide `reason_en` (the four hard-denied actions, and any
`on_condition_fail.reason_en` an operator adds later), that value always wins.
"""
from __future__ import annotations

REASON_CODE_EN: dict[str, str] = {
    "action_not_in_policy": (
        "This action is not covered by any defined policy, so I cannot carry it out."
    ),
    "action_not_allowed": "I am not authorised to do this, so I am passing it to the relevant team.",
    "condition_failed": "This action cannot be carried out because its conditions are not met right now.",
    "amount_above_limit": "The requested amount exceeds the permitted limit.",
    "rate_limit_exceeded": "The permitted number of attempts for this action has been reached.",
    "provisioning_retry_limit": (
        "The retry limit has been reached, so I am passing this to the Subscription "
        "Operations team."
    ),
    "credit_not_applicable": (
        "I cannot apply compensation for this outage, so I am passing it to the Billing team."
    ),
    "action_allowed": "This action complies with policy, so I can carry it out.",
    "action_allowed_requires_confirmation": (
        "This action cannot be undone, so I need your approval before going ahead."
    ),
}

_DEFAULT_DENIAL_EN = REASON_CODE_EN["condition_failed"]


def reason_en_for(reason_code: str | None, override: str | None = None) -> str:
    """Resolve the customer-facing sentence for a reason code.

    `override` (typically `ActionPolicy.reason_en` or `OnConditionFail`-adjacent text taken
    straight from `policy.yaml`) always wins when present.
    """
    if override:
        return override
    if reason_code and reason_code in REASON_CODE_EN:
        return REASON_CODE_EN[reason_code]
    return _DEFAULT_DENIAL_EN

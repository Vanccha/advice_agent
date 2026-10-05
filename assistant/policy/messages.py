"""Reason-code → Turkish sentence fallback table (contracts §4.6).

Used by `PolicyEngine` whenever `policy.yaml` does not already carry an explicit `reason_tr`
for the situation (e.g. a generic condition failure, an unknown action, a rate limit hit).
When the policy file *does* provide `reason_tr` (the four hard-denied actions, and any
`on_condition_fail.reason_tr` an operator adds later), that value always wins.
"""
from __future__ import annotations

REASON_CODE_TR: dict[str, str] = {
    "action_not_in_policy": (
        "Bu işlem tanımlı politikalar arasında yer almıyor, bu nedenle gerçekleştiremiyorum."
    ),
    "action_not_allowed": "Bu işlem için yetkim yok, ilgili ekibe aktarıyorum.",
    "condition_failed": "Bu işlem şu an gerekli koşulları sağlamadığı için gerçekleştirilemiyor.",
    "amount_above_limit": "Talep edilen tutar izin verilen üst sınırı aşıyor.",
    "rate_limit_exceeded": "Bu işlem için izin verilen deneme sayısına ulaşıldı.",
    "provisioning_retry_limit": (
        "Yeniden deneme sayısı sınırına ulaşıldığı için bu işlemi Abonelik İşlemleri "
        "ekibine aktarıyorum."
    ),
    "credit_not_applicable": (
        "Bu kesinti için telafi tanımlayamıyorum, Faturalama ekibine aktarıyorum."
    ),
    "action_allowed": "Bu işlem politika kurallarına uygun, gerçekleştirebilirim.",
    "action_allowed_requires_confirmation": (
        "Bu işlem geri alınamaz olduğu için devam etmeden önce onayınız gerekiyor."
    ),
}

_DEFAULT_DENIAL_TR = REASON_CODE_TR["condition_failed"]


def reason_tr_for(reason_code: str | None, override: str | None = None) -> str:
    """Resolve the Turkish sentence for a reason code.

    `override` (typically `ActionPolicy.reason_tr` or `OnConditionFail`-adjacent text taken
    straight from `policy.yaml`) always wins when present.
    """
    if override:
        return override
    if reason_code and reason_code in REASON_CODE_TR:
        return REASON_CODE_TR[reason_code]
    return _DEFAULT_DENIAL_TR

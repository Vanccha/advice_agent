"""`get_decision_service()` selects a `DecisionService` implementation by
`DECISION_SERVICE` (contracts §4.5: `llm_structured | typesafe_jev`).

Import as: ``from decision.factory import get_decision_service``.
"""
from __future__ import annotations

from core_common.config import TenantConfig
from core_common.settings import AssistantSettings, get_settings
from decision.base import DecisionService
from decision.llm_structured import LLMStructuredDecisionService
from decision.typesafe_jev import TypeSafeJevDecisionService
from llm.base import LLMProvider


def get_decision_service(
    provider: LLMProvider,
    settings: AssistantSettings | None = None,
    tenant_config: TenantConfig | None = None,
) -> DecisionService:
    settings = settings or get_settings()
    name = settings.DECISION_SERVICE

    if name == "llm_structured":
        return LLMStructuredDecisionService(provider)
    if name == "typesafe_jev":
        return TypeSafeJevDecisionService(provider=provider, tenant_config=tenant_config)
    raise ValueError(
        f"Unknown DECISION_SERVICE: {name!r}. Expected 'llm_structured' or 'typesafe_jev'."
    )

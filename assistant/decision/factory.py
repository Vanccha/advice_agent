"""``get_decision_service()``: choose a ``DecisionService`` by ``DECISION_SERVICE``.

Where this sits: ``decision`` package; called at start-up by ``api``/evals with an already
guarded ``LLMProvider``. Values: ``llm_structured`` (default) or ``typesafe_jev`` (stub).

Key ideas:
- Factory keyed by configuration: no ``if name == ...`` scattered through callers.
- Unknown names fail loudly at start-up rather than at the first customer turn.

Read next: ``decision/llm_structured.py``.
See: docs/contracts.md §4.5.
"""
from __future__ import annotations

from core_common.config_models import TenantConfig
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

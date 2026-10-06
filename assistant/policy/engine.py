"""The authority engine (contracts §4.6). The model never decides its own authority: every
action name is checked here against `config/tenants/<tenant>/policy.yaml` before it may run.

Import as: ``from policy.engine import PolicyEngine``.
"""
from __future__ import annotations

from typing import Any

from core_common.config import ActionPolicy, Condition, PolicyFile
from core_common.types import Department, PolicyDecision

from policy.messages import reason_en_for

_MISSING = object()


def _get_path(context: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path (`"job.status"`) against a nested dict context."""
    node: Any = context
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def _condition_holds(condition: Condition, context: dict[str, Any]) -> bool:
    value = _get_path(context, condition.field)

    if condition.exists is not None:
        present = value is not _MISSING and value is not None
        if present != condition.exists:
            return False
        if condition.exists is False:
            # Asked only "must be absent" — nothing else to compare.
            return True

    if value is _MISSING:
        # Any other operator against data we don't have: fail safe (deny), never guess.
        return False

    if condition.eq is not None and value != condition.eq:
        return False
    if condition.ne is not None and value == condition.ne:
        return False
    if condition.in_ is not None and value not in condition.in_:
        return False
    if condition.lt is not None and not (value < condition.lt):
        return False
    if condition.lte is not None and not (value <= condition.lte):
        return False
    if condition.gt is not None and not (value > condition.gt):
        return False
    if condition.gte is not None and not (value >= condition.gte):
        return False
    return True


def _department(name: str | None) -> Department | None:
    if not name:
        return None
    return Department(name)


class PolicyEngine:
    """Interprets one tenant's `policy.yaml`. Stateless except for the loaded config."""

    def __init__(self, policy_config: PolicyFile) -> None:
        self._policy = policy_config

    @property
    def tenant(self) -> str:
        return self._policy.tenant

    # -- public API ----------------------------------------------------------------

    def describe_allowed_actions(self) -> list[str]:
        """Names of actions whose top-level `allowed` flag is true (conditions aside)."""
        return [name for name, cfg in self._policy.actions.items() if cfg.allowed]

    def check(self, action_name: str, context: dict[str, Any] | None = None) -> PolicyDecision:
        context = context or {}
        action_cfg = self._policy.actions.get(action_name)

        if action_cfg is None:
            return PolicyDecision(
                allowed=False,
                requires_confirmation=False,
                reason_code="action_not_in_policy",
                reason_en=reason_en_for("action_not_in_policy"),
                escalate_to=None,
            )

        if not action_cfg.allowed:
            reason_code = action_cfg.reason_code or "action_not_allowed"
            return PolicyDecision(
                allowed=False,
                requires_confirmation=False,
                reason_code=reason_code,
                reason_en=reason_en_for(reason_code, action_cfg.reason_en),
                escalate_to=_department(action_cfg.escalate_to),
            )

        condition_failure = self._first_failing_condition(action_cfg, context)
        if condition_failure is not None:
            return condition_failure

        rate_limit_failure = self._rate_limit_failure(action_cfg, context)
        if rate_limit_failure is not None:
            return rate_limit_failure

        amount_failure = self._amount_failure(action_cfg, context)
        if amount_failure is not None:
            return amount_failure

        requires_confirmation = action_cfg.requires_confirmation
        reason_code = (
            "action_allowed_requires_confirmation" if requires_confirmation else "action_allowed"
        )
        return PolicyDecision(
            allowed=True,
            requires_confirmation=requires_confirmation,
            reason_code=reason_code,
            reason_en=reason_en_for(reason_code),
            escalate_to=None,
        )

    # -- internal checks -------------------------------------------------------------

    def _first_failing_condition(
        self, action_cfg: ActionPolicy, context: dict[str, Any]
    ) -> PolicyDecision | None:
        for condition in action_cfg.conditions:
            if _condition_holds(condition, context):
                continue
            on_fail = action_cfg.on_condition_fail
            reason_code = (on_fail.reason_code if on_fail else None) or "condition_failed"
            escalate_to = _department(on_fail.escalate_to) if on_fail else None
            return PolicyDecision(
                allowed=False,
                requires_confirmation=False,
                reason_code=reason_code,
                reason_en=reason_en_for(reason_code),
                escalate_to=escalate_to,
                details={"failed_condition": condition.field},
            )
        return None

    def _rate_limit_failure(
        self, action_cfg: ActionPolicy, context: dict[str, Any]
    ) -> PolicyDecision | None:
        rate_limit = action_cfg.rate_limit
        if rate_limit is None:
            return None
        usage = context.get("usage", {}) or {}

        if rate_limit.per_conversation is not None:
            used = usage.get("per_conversation", 0) or 0
            if used >= rate_limit.per_conversation:
                return PolicyDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason_code="rate_limit_exceeded",
                    reason_en=reason_en_for("rate_limit_exceeded"),
                    escalate_to=None,
                    limit_applied="per_conversation",
                )

        if rate_limit.per_customer_per_day is not None:
            used = usage.get("per_customer_per_day", 0) or 0
            if used >= rate_limit.per_customer_per_day:
                return PolicyDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason_code="rate_limit_exceeded",
                    reason_en=reason_en_for("rate_limit_exceeded"),
                    escalate_to=None,
                    limit_applied="per_customer_per_day",
                )
        return None

    def _amount_failure(
        self, action_cfg: ActionPolicy, context: dict[str, Any]
    ) -> PolicyDecision | None:
        if action_cfg.max_amount_gbp is None:
            return None
        amount = context.get("amount_gbp")
        if amount is None:
            return None
        if amount > action_cfg.max_amount_gbp:
            on_fail = action_cfg.on_condition_fail
            escalate_to = _department(on_fail.escalate_to) if on_fail else None
            return PolicyDecision(
                allowed=False,
                requires_confirmation=False,
                reason_code="amount_above_limit",
                reason_en=reason_en_for("amount_above_limit"),
                escalate_to=escalate_to,
                limit_applied=f"max_amount_gbp={action_cfg.max_amount_gbp}",
            )
        return None

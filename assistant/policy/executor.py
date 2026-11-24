"""``ActionExecutor``: the only legal path from "the model wants X" to "X happened".

Where this sits: ``policy`` package. ``modes.action`` (via ``mcp_gateway.action_runner``) calls
``execute()``; it calls ``PolicyEngine.check``, the ``AuditLog`` and the injected action runner.

Key ideas:
- Enforcement by construction: there is no code path that calls the action runner without a
  prior policy check, and every check is written to the audit log.
- Two refusal exceptions, so callers can tell them apart: ``PolicyDenied`` (never) and
  ``ConfirmationRequired`` (allowed once the customer approves).
- Denials and confirmations are recorded as ``ActionRecord`` rows too, not only successes.
- The executor, not the caller, supplies the facts that only the assistant knows: usage
  counters for rate limits (``policy.usage``) and the per-conversation action budget.

Read next: ``policy/engine.py``, ``audit/log.py``, ``mcp_gateway/action_runner.py``.
See: docs/contracts.md §4.6.
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

from core_common.db import session_scope
from core_common.models import ActionRecord, Conversation
from core_common.types import PolicyDecision, StepType

from policy.engine import PolicyEngine
from policy.usage import action_usage, count_executed_actions

ActionRunner = Callable[[str, dict[str, Any]], dict[str, Any]]


class PolicyDenied(Exception):
    """Raised when `PolicyEngine.check()` denies an action outright."""

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason_en or decision.reason_code or "policy denied")
        self.decision = decision


class ConfirmationRequired(Exception):
    """Raised when an action is allowed but needs explicit user confirmation first."""

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason_en or "confirmation required")
        self.decision = decision


class ActionExecutor:
    """Runs actions only after policy allows them, auditing every step.

    Args:
        policy_engine: decides allow / deny / needs-confirmation.
        audit_log: receives a record for the policy check and for the outcome.
        action_runner: the injected callable that actually performs the action (in
            production it calls the company API through the MCP gateway).
        conversation_action_guard: called with the number of actions already executed in
            the conversation once policy has allowed an action; it raises to refuse
            (``StateMachine.check_actions_per_conversation`` raises ``LimitExceeded``).
            Policy decides *what* may run, the guard caps *how often* per conversation.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        audit_log: Any,
        action_runner: ActionRunner,
        conversation_action_guard: Callable[[int], None] | None = None,
    ) -> None:
        self._policy_engine = policy_engine
        self._audit_log = audit_log
        self._action_runner = action_runner
        self._conversation_action_guard = conversation_action_guard

    def execute(
        self,
        action_name: str,
        params: dict[str, Any],
        context: dict[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Check policy, then run (or refuse to run) `action_name`.

        `context` doubles as both the policy-condition context (dotted paths like
        `job.status`) and the audit-linkage context: optional `conversation_id`, `tenant`,
        `actor` keys identify the conversation these records belong to. Any `usage` key the
        caller passes is overwritten with counters read from our own action records.

        Raises `PolicyDenied`, `ConfirmationRequired`, or whatever the conversation guard
        raises (`LimitExceeded`) when the conversation already used its action budget.
        """
        conversation_id = str(context.get("conversation_id", "unknown"))
        tenant = str(context.get("tenant") or self._policy_engine.tenant)
        actor = str(context.get("actor", "assistant"))

        customer_no = params.get("customer_no") or self._conversation_customer(conversation_id)
        context = {
            **context,
            "usage": action_usage(
                self._audit_log.session_factory,
                conversation_id=conversation_id,
                customer_no=customer_no,
                action_name=action_name,
            ),
        }
        decision = self._policy_engine.check(action_name, context)

        self._audit_log.append(
            conversation_id,
            StepType.POLICY_CHECK,
            f"policy check for action '{action_name}'",
            decision.reason_en,
            {"action_name": action_name},
            tenant=tenant,
            actor=actor,
            policy_decision=decision.model_dump(mode="json"),
        )

        if not decision.allowed:
            self._record_action(conversation_id, action_name, params, decision, executed=False)
            raise PolicyDenied(decision)

        if self._conversation_action_guard is not None:
            # Checked before asking for confirmation too: do not ask the customer to approve
            # something that would be refused for budget reasons a moment later.
            self._conversation_action_guard(
                count_executed_actions(self._audit_log.session_factory, conversation_id)
            )

        if decision.requires_confirmation and not confirmed:
            self._audit_log.append(
                conversation_id,
                StepType.APPROVAL_REQUESTED,
                f"confirmation requested for action '{action_name}'",
                decision.reason_en,
                {"action_name": action_name},
                tenant=tenant,
                actor=actor,
                policy_decision=decision.model_dump(mode="json"),
            )
            self._record_action(conversation_id, action_name, params, decision, executed=False)
            raise ConfirmationRequired(decision)

        result = self._action_runner(action_name, params)

        self._record_action(conversation_id, action_name, params, decision, executed=True, result=result)
        self._audit_log.append(
            conversation_id,
            StepType.ACTION,
            f"executed action '{action_name}'",
            decision.reason_en,
            {"action_name": action_name},
            tenant=tenant,
            actor=actor,
            policy_decision=decision.model_dump(mode="json"),
        )
        return result

    def _conversation_customer(self, conversation_id: str) -> str | None:
        """The customer a stored conversation belongs to, when the params do not name one."""
        with session_scope(self._audit_log.session_factory) as session:
            return session.scalar(
                select(Conversation.customer_no).where(Conversation.conversation_id == conversation_id)
            )

    def _record_action(
        self,
        conversation_id: str,
        action_name: str,
        params: dict[str, Any],
        decision: PolicyDecision,
        *,
        executed: bool,
        result: dict[str, Any] | None = None,
    ) -> None:
        with session_scope(self._audit_log.session_factory) as session:
            session.add(
                ActionRecord(
                    conversation_id=conversation_id,
                    action_name=action_name,
                    params=params,
                    policy_allowed=decision.allowed,
                    policy_reason=decision.reason_code,
                    executed=executed,
                    result=result,
                )
            )

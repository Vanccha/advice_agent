"""Per-turn gateway scoping and the current message being visible to decision calls."""
from __future__ import annotations

from typing import Any

from core_common.db import session_scope
from core_common.models import Conversation
from llm.scripted import ScriptedProvider
from decision.llm_structured import LLMStructuredDecisionService
from modes.tests.conftest import build_orchestrator, make_gateway

COMPLAINT = "I was charged twice, there is a wrong charge on my bill"


def test_tool_calls_are_audited_under_the_real_conversation(tenant_config, session_factory) -> None:
    gateway = make_gateway()
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    gateway._audit_log = orch.audit_log  # audit like the real gateway does

    result = orch.handle_message(conversation_id="conv-real-1", customer_no="NS-100001", message=COMPLAINT)

    timeline = orch.audit_log.timeline("conv-real-1")
    assert [e for e in timeline if e.step_type == "tool_call"], (
        "tool calls must show up in the conversation's audit timeline"
    )
    assert not [e for e in orch.audit_log.timeline("fake-conversation") if e.step_type == "tool_call"]
    assert orch.audit_log.verify_chain(result.conversation_id).ok


def test_turns_have_independent_call_budgets(tenant_config, session_factory) -> None:
    gateway = make_gateway(max_calls_per_turn=3)
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)

    ctx_a = orch._build_ctx("conv-a", "NS-100001")
    ctx_b = orch._build_ctx("conv-b", "NS-100001")
    assert ctx_a.gateway is not ctx_b.gateway
    assert ctx_a.gateway.conversation_id == "conv-a"
    assert ctx_b.gateway.conversation_id == "conv-b"

    for _ in range(3):
        ctx_a.gateway.call_sync("find_customer", {"customer_no": "NS-100001"})
    # A's budget is spent; B is unaffected, and a new turn for A starts fresh.
    ctx_b.gateway.call_sync("find_customer", {"customer_no": "NS-100001"})
    assert ctx_b.gateway._calls_this_turn == 1
    assert orch._build_ctx("conv-a").gateway._calls_this_turn == 0
    # The shared root records every call but its own budget is never consumed.
    assert gateway._calls_this_turn == 0
    assert len(gateway.calls_made) == 4


class _RecordingDecisionService(LLMStructuredDecisionService):
    def __init__(self, provider: ScriptedProvider) -> None:
        super().__init__(provider)
        self.seen: dict[str, list[list[dict[str, str]]]] = {}

    def _record(self, name: str, ctx: Any) -> None:
        self.seen.setdefault(name, []).append(list(ctx.history))

    def classify_issue_type(self, ctx):
        self._record("classify_issue_type", ctx)
        return super().classify_issue_type(ctx)

    def assess_urgency(self, ctx):
        self._record("assess_urgency", ctx)
        return super().assess_urgency(ctx)

    def choose_department(self, ctx):
        self._record("choose_department", ctx)
        return super().choose_department(ctx)

    def triage(self, ctx):
        self._record("triage", ctx)
        return super().triage(ctx)

    def classify_intent(self, ctx):
        self._record("classify_intent", ctx)
        return super().classify_intent(ctx)


def test_decision_calls_see_the_current_message_once(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "detect_duplicate_charges": {"duplicates": [{"payment_ids": [88, 89]}]},
        "create_structured_ticket": {
            "ticket_key": "TKT-2026-00061", "department": "BILLING", "status": "NEW",
            "priority": "HIGH", "created": True,
        },
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    service = _RecordingDecisionService(orch.provider)
    orch.decision_service = service

    orch.handle_message(conversation_id="conv-hist", customer_no="NS-100001", message=COMPLAINT)

    assert service.seen.get("classify_intent"), "classify_intent was not called"
    for name, histories in service.seen.items():
        for history in histories:
            assert history[-1] == {"role": "user", "content": COMPLAINT}, name
            assert sum(1 for m in history if m["content"] == COMPLAINT) == 1, name
    # At least one of the action-mode decision calls ran for this complaint.
    assert set(service.seen) - {"classify_intent"}, "no action-mode decision call ran"

    with session_scope(session_factory) as session:
        state = session.query(Conversation).filter_by(conversation_id="conv-hist").one().state
    assert [m["role"] for m in state["history"]] == ["user", "assistant"]
    assert [m["content"] for m in state["history"]].count(COMPLAINT) == 1

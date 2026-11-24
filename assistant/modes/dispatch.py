"""Mode routing for one turn: run the handler for the current mode, follow the hand-offs.

Where this sits: called by `modes.orchestrator` once per customer message, after the state
is loaded and before it is saved. Calls the mode handlers (`router`, `advisory`,
`diagnostic`, `investigation`, `status_query`, `action`) and asks `StateMachine` to approve
every mode change.

Key ideas:
- A loop, not a call chain: a handler that only decides "go to mode X" (the router, the
  diagnostic checklist) lets the loop continue in X within the same turn, so the customer
  gets one reply instead of an empty one.
- The loop is bounded (`_MAX_HOPS`) so a bug in a transition can never spin forever.
- Handlers never move the conversation themselves; the state machine validates each move,
  so an illegal transition fails loudly instead of corrupting the flow.

Read next: modes/machine.py, modes/router.py, modes/action/.

See: docs/contracts.md §4.3.
"""
from __future__ import annotations

from core_common.types import AdvisoryProfile, Diagnosis, Mode, RootCause
from modes import action, advisory, diagnostic, investigation, replies, status_query
from modes.context import TurnContext
from modes.machine import StateMachine
from modes.results import StepOutcome
from modes.router import route_intent
from modes.state import ConversationState
from modes.tool_data import first_record

_MAX_HOPS = 5


def lookup_requester(ctx: TurnContext, customer_no: str | None) -> tuple[str, str]:
    """Name and contact for a ticket's requester block; neutral placeholders when unknown.

    Cached per turn: several handlers need the customer record and each lookup would
    otherwise spend the turn's tool-call budget.
    """
    if not customer_no:
        return "Customer", "unknown"
    outcome = ctx.call_tool_cached("find_customer", {"customer_no": customer_no})
    data = first_record(outcome)
    if data is not None:
        name = data.get("full_name") or "Customer"
        contact = data.get("phone") or data.get("email") or "unknown"
        return str(name), str(contact)
    return "Customer", "unknown"


def dispatch_turn(
    ctx: TurnContext,
    machine: StateMachine,
    state: ConversationState,
    mode: Mode,
    masked_message: str,
    customer_no: str | None,
) -> tuple[Mode, StepOutcome]:
    """Handle one message starting in `mode`; return the next mode and what to tell the
    customer. Mutates `state` (advisory profile, diagnosis, investigation counters)."""
    diagnosis: Diagnosis | None = None
    finding: investigation.InvestigationFinding | None = None
    hops = 0

    while True:
        hops += 1
        if hops > _MAX_HOPS:
            return Mode.CLOSING, StepOutcome(reply_en=replies.TOO_MANY_HOPS)

        if mode is Mode.ROUTER:
            router_result = route_intent(ctx, masked_message)
            mode = machine.transition(Mode.ROUTER, router_result.next_mode)
            if router_result.reply_en is not None:
                return mode, StepOutcome(reply_en=router_result.reply_en)
            if mode is Mode.ADVISORY:
                # Keep what the customer already told us about their household: re-entering
                # advisory after a recommendation means they are refining, and asking the
                # same questions again reads as amnesia. Only per-round bookkeeping resets.
                state.advisory_awaiting_field = None
                state.advisory_questions_asked = 0
                state.advisory_first_turn = not state.advisory_profile
            continue

        if mode is Mode.ADVISORY:
            is_first = state.advisory_first_turn
            state.advisory_first_turn = False
            step = advisory.handle_turn(
                ctx,
                profile=AdvisoryProfile.model_validate(state.advisory_profile),
                awaiting_field=state.advisory_awaiting_field,
                questions_asked=state.advisory_questions_asked,
                masked_message=masked_message,
                is_first_turn=is_first,
            )
            state.advisory_profile = step.profile.model_dump(mode="json")
            state.advisory_awaiting_field = step.awaiting_field
            state.advisory_questions_asked = step.questions_asked
            target = Mode.CLOSING if step.done else Mode.ADVISORY
            return machine.transition(Mode.ADVISORY, target), StepOutcome(reply_en=step.reply_en)

        if mode is Mode.DIAGNOSTIC:
            if not customer_no:
                return Mode.CLOSING, StepOutcome(reply_en=replies.SIGN_IN_FOR_DIAGNOSIS)
            reused = state.diagnosis if state.has_open_investigation() else None
            if reused is not None and reused.root_cause == RootCause.NO_ISSUE_FOUND:
                # An open investigation already ran the checklist and found nothing; reuse
                # it instead of spending the turn's tool budget again.
                diagnosis = reused
            else:
                diagnosis = diagnostic.run_diagnosis(ctx, customer_no)
                state.diagnosis = diagnosis
            if diagnosis.root_cause == RootCause.NO_ISSUE_FOUND:
                # No known pattern: read what the customer actually wrote (bounded,
                # read-only, audited). It either asks one question and stays in DIAGNOSTIC,
                # or hands a finding to ACTION.
                inv_turn = investigation.plan_turn(
                    ctx, state, customer_no=customer_no, diagnosis=diagnosis
                )
                if inv_turn.reply_en is not None:
                    stay = machine.transition(Mode.DIAGNOSTIC, Mode.DIAGNOSTIC)
                    return stay, StepOutcome(
                        reply_en=inv_turn.reply_en, diagnosis=diagnosis.model_dump(mode="json")
                    )
                finding = inv_turn.finding
            else:
                state.clear_investigation()
            mode = machine.transition(Mode.DIAGNOSTIC, Mode.ACTION)
            continue

        if mode is Mode.STATUS_QUERY:
            if not customer_no:
                return Mode.CLOSING, StepOutcome(reply_en=replies.SIGN_IN_FOR_STATUS)
            sq_step = status_query.handle_status_query(ctx, customer_no)
            new_mode = machine.transition(Mode.STATUS_QUERY, sq_step.next_mode)
            return new_mode, StepOutcome(reply_en=sq_step.reply_en)

        if mode is Mode.ACTION:
            # ACTION is normally entered from DIAGNOSTIC in this same loop; the stored
            # diagnosis is the safety net if a conversation ever resumes directly here.
            diagnosis = diagnosis or state.diagnosis
            if diagnosis is None:
                raise RuntimeError("ACTION mode reached without a diagnosis")
            name, contact = lookup_requester(ctx, customer_no)
            action_step = action.handle_action(
                ctx,
                diagnosis=diagnosis,
                customer_no=customer_no or "unknown",
                requester_name=name,
                requester_contact=contact,
                finding=finding,
            )
            new_mode = machine.transition(Mode.ACTION, action_step.next_mode)
            return new_mode, StepOutcome(
                reply_en=action_step.reply_en,
                actions=action_step.actions,
                ticket_key=action_step.ticket_key,
                requires_approval=action_step.requires_approval,
                approval_id=action_step.approval_id,
                diagnosis=diagnosis.model_dump(mode="json"),
            )

        if mode is Mode.AWAITING_APPROVAL:
            return mode, StepOutcome(reply_en=replies.USE_APPROVAL_CARD)

        # ESCALATED / CLOSING mid-loop should not happen (the orchestrator resets them to
        # ROUTER before dispatching); stop safely rather than guess.
        return mode, StepOutcome(reply_en=replies.CONVERSATION_COMPLETE)

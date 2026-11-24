"""Customer-facing reply catalogue: every fixed sentence the assistant says in its own words.

Where this sits: pure data and tiny formatting functions with no imports from the rest of
`modes/`. Mode handlers (`router`, `status_query`, `advisory`, `action`, `investigation`)
and the orchestration modules (`dispatch`, `approvals`, `events`, `orchestrator`) import
from here instead of embedding English in their logic.

Key ideas:
- Separate wording from logic: a handler decides WHAT to say (a constant or function name),
  this module owns HOW it is worded.
- This is the seam for translations. A future Turkish locale replaces this module (or
  selects a second one by the tenant's language) without touching any handler.
- Wording is asserted by evals and tests (they look for keywords), so change text
  deliberately. Replies are British English; money is GBP.

Not covered here: replies composed by the model (investigation findings, advisory
narration) and ticket text read by departments (kept next to the ticket builders).

Read next: modes/router.py, modes/dispatch.py, modes/action/.
"""
from __future__ import annotations

# --- Router -------------------------------------------------------------------------
SMALLTALK = (
    "Hello! I can help with package recommendations, diagnosing a fault, or the status of "
    "an existing request. How can I help?"
)
OUT_OF_SCOPE = (
    "I cannot help with that, as I can only assist with NetSwift internet services. "
    "Feel free to ask about your broadband package, your subscription or an existing request."
)
HANDOVER_UNSURE = (
    "I was not quite sure what you need help with, so I am passing you to an agent."
)
# Distinct from HANDOVER_UNSURE: when the model is unreachable, "I did not understand you"
# would blame a customer who wrote perfectly clearly.
HANDOVER_PROVIDER_DOWN = (
    "I cannot reach my AI service right now, so I am unable to look at your request. "
    "I am passing you to an agent; you are also welcome to try again shortly."
)

# --- Turn lifecycle (limits and dead ends) ------------------------------------------
STEP_LIMIT_REACHED = (
    "I have reached the number of steps allowed in this conversation, so "
    "I am passing you to an agent."
)
TOOL_BUDGET_REACHED = (
    "I reached my lookup limit before finishing my checks. I am passing "
    "this to an agent, who will get back to you as soon as possible."
)
TOO_MANY_HOPS = "I am passing this conversation to an agent."
USE_APPROVAL_CARD = "Please use one of the options on the approval card."
CONVERSATION_COMPLETE = "This conversation is complete. Feel free to write about something new."
SIGN_IN_FOR_DIAGNOSIS = (
    "Please sign in (with your customer number) first so that I can diagnose the problem."
)
SIGN_IN_FOR_STATUS = "Please sign in first so that I can check your status."

# --- Status query -------------------------------------------------------------------
SUBSCRIPTION_STATUS = {
    "registered": "registered",
    "awaiting_payment": "awaiting payment",
    "payment_received": "payment received, waiting for setup",
    "provisioning": "setup in progress",
    "provisioned": "setup complete, waiting for an appointment",
    "installation_scheduled": "installation appointment booked",
    "active": "active",
    "suspended": "suspended",
    "cancelled": "cancelled",
}
TICKET_STATUS = {
    "NEW": "new",
    "TRIAGE": "under review",
    "IN_PROGRESS": "in progress",
    "WAITING_CUSTOMER": "waiting for information from you",
    "RESOLVED": "resolved",
    "CLOSED": "closed",
    "REJECTED": "rejected",
}
NO_OPEN_REQUESTS = "I cannot see any open requests for you."


def latest_request_status(ticket_key: str, status_en: str) -> str:
    return f"Your most recent request is {ticket_key}: status '{status_en}'."


def subscription_status_line(status_en: str, price_part: str) -> str:
    return f"Your subscription status: {status_en}{price_part}."


# --- Ticket status webhook ----------------------------------------------------------
def ticket_updated(ticket_key: str, status_en: str, comment: str | None = None) -> str:
    """`comment` must already be masked by the caller."""
    reply = f"Your request ({ticket_key}) has been updated: {status_en}."
    if comment:
        reply += f" Not: {comment}"
    return reply


# --- Monitoring alert ---------------------------------------------------------------
def payment_system_unavailable(ticket_key: str) -> str:
    return (
        "The payment system is temporarily unavailable. The technical team has been "
        f"notified; reference number: {ticket_key}."
    )


# --- Approvals ----------------------------------------------------------------------
APPROVAL_DENIED = "Understood, I have not carried out that action."
APPROVAL_GRANTED = "Thank you for approving; I have completed the action."
DEFAULT_APPROVAL_PROMPT = "This action needs your approval."


def approved_action_blocked(ticket_key: str) -> str:
    return (
        "I could not complete the action, so I have passed your request to the relevant "
        f"team: {ticket_key}."
    )


def approval_question(reason_en: str | None) -> str:
    return f"{reason_en} Do you approve?"


# --- Advisory -----------------------------------------------------------------------
ADVISORY_NO_MATCH = (
    "I could not find a package that fits what you told me. Would you like to stretch "
    "your budget or needs a little?"
)
ADVISORY_INTRO = "Here are the packages that best fit your needs:"
ADVISORY_QUESTION_LIMIT_NOTE = (
    "\n\n(I have reached the number of questions I can ask, so I recommended based on "
    "what I know.)"
)


def advisory_recommendation(best_name: str) -> str:
    return f"\n\nMy recommendation: {best_name}."


# --- Investigation ------------------------------------------------------------------
INVESTIGATION_ASK_MORE_DETAIL = (
    "I have checked your account, subscription, payments and our network, and nothing there "
    "explains this yet. Could you tell me a little more? For example, what exactly is "
    "happening, when it started, and which device or light is affected?"
)
INVESTIGATION_NO_QUESTION = (
    "I could not work out the cause from what I can see, so I am passing this to a colleague."
)
INVESTIGATION_ROUNDS_EXHAUSTED = (
    "I still cannot pin down the cause from what I can see, so I am passing "
    "this to a colleague who can look into it properly."
)
INVESTIGATION_LOW_CONFIDENCE = (
    "I could not be certain about the cause from what I can see, so I am "
    "passing this to a colleague to check."
)
INVESTIGATION_FALLBACK = (
    "I could not work out the cause from what I can see, so I am passing this to "
    "a colleague who can look into it properly."
)

# --- Action: nothing to do / cannot proceed -----------------------------------------
NO_OBVIOUS_PROBLEM = (
    "I have checked your account and subscription, and I cannot see an obvious "
    "problem right now. If it carries on, please share a few more details."
)
CUSTOMER_NOT_FOUND = "I could not find your customer number; please check it and try again."
NO_AUTOMATIC_STEP = (
    "I have looked into it, but there is no step I can take automatically; if you "
    "like, I can pass you to an agent."
)

# --- Action: automatic fixes --------------------------------------------------------
ACTION_SUCCESS = {
    "retry_provisioning_job": (
        "I found that your setup job had got stuck, and I have restarted it just now. I "
        "expect it to finish within a few minutes."
    ),
    "enqueue_provisioning_job": (
        "I can see your payment was received but the setup process never started; I have "
        "now queued your setup job."
    ),
}


def action_success(action_name: str) -> str:
    return ACTION_SUCCESS.get(action_name, f"I have successfully carried out {action_name}.")


# --- Action: handover to a department -----------------------------------------------
# What the customer is told we found before being told it was handed over. "I am not
# authorised, I forwarded it" alone leaves them none the wiser (and during a payment
# outage lets them assume their card is at fault).
FINDING = {
    "stuck_provisioning": "I found that your setup (provisioning) job has got stuck.",
    "paid_not_active": (
        "I found that your payment was received, but your subscription has not been activated."
    ),
    "double_charge": "I found that the same amount was taken from your account twice.",
    "missed_installation": "I found that your installation appointment did not take place.",
    "regional_outage": (
        "I found an ongoing infrastructure fault in your area; the problem is not specific to you."
    ),
    "payment_system_down": (
        "Our payment system is temporarily unavailable. The problem is not with your card or "
        "your device; you can try your payment again once the system is back to normal."
    ),
    "no_issue_found": "I could not pin down the cause of the problem in your records.",
    "customer_not_found": "I could not reach your records.",
}
FINDING_DEFAULT = "I have looked into your situation."


def finding(root_cause: str) -> str:
    return FINDING.get(root_cause, FINDING_DEFAULT)


def blocked_by_policy_handover(root_cause: str, department_en: str, ticket_key: str) -> str:
    return (
        f"{finding(root_cause)} I am not authorised to complete this myself, "
        f"so I have passed your request to the {department_en} team. "
        f"Your reference number is {ticket_key}."
    )


def investigation_handover(reply: str, department_en: str, ticket_key: str) -> str:
    return (
        f"{reply} I have passed this to the {department_en} team with everything I found, so you "
        f"will not need to explain it again. Your reference number is {ticket_key}."
    )


# --- Action: regional outage --------------------------------------------------------
def _incident_attached(incident_no: str, ticket_key: str) -> str:
    return (
        f"There is an open fault record for your area ({incident_no}), and I "
        f"have added your request to ticket {ticket_key}."
    )


def outage_credit_offer(incident_no: str, ticket_key: str) -> str:
    return (
        f"{_incident_attached(incident_no, ticket_key)} I can apply a "
        "goodwill credit of up to £5 to your account for the outage, but as this cannot "
        "be undone I need your approval first. Do you approve?"
    )


def outage_credit_denied(incident_no: str, ticket_key: str, reason_en: str | None) -> str:
    return (
        f"{_incident_attached(incident_no, ticket_key)} I cannot apply "
        f"a credit for this outage: {reason_en}"
    )


def outage_attached_only(incident_no: str, ticket_key: str) -> str:
    return _incident_attached(incident_no, ticket_key)

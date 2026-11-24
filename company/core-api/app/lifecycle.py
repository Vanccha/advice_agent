"""The subscription state machine: which status may follow which.

Where this sits: called by routers and the webhook handler; every status
change goes through `transition`.

Key ideas:
- State transitions as an explicit table: `ALLOWED_TRANSITIONS` is data, so
  the rules can be read, tested and changed in one place, and an illegal move
  fails with `ILLEGAL_TRANSITION` (409) instead of corrupting state.
- Audit trail: each change also writes a `SubscriptionEvent` (who, why,
  from, to) in the same transaction, which is what the diagnostic timeline
  view reads.

Read next: `routers/subscriptions.py`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from shared.clock import utcnow
from shared.errors import Conflict

from app.models import Subscription, SubscriptionEvent

# Allowed forward transitions, plus active <-> suspended and "any -> cancelled".
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "registered": {"awaiting_payment", "cancelled"},
    "awaiting_payment": {"payment_received", "cancelled"},
    "payment_received": {"provisioning", "cancelled"},
    "provisioning": {"provisioned", "cancelled"},
    "provisioned": {"installation_scheduled", "cancelled"},
    "installation_scheduled": {"active", "cancelled"},
    "active": {"suspended", "cancelled"},
    "suspended": {"active", "cancelled"},
    "cancelled": set(),
}


def assert_transition(from_status: str, to_status: str) -> None:
    """Raise `ILLEGAL_TRANSITION` (409) unless `to_status` may follow `from_status`."""
    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise Conflict(
            "ILLEGAL_TRANSITION",
            f"Cannot move subscription from '{from_status}' to '{to_status}'.",
            from_status=from_status,
            to_status=to_status,
        )


def transition(
    session: Session,
    subscription: Subscription,
    to_status: str,
    *,
    actor: str,
    reason: str | None = None,
    event_type: str = "status_change",
    payload: dict | None = None,
    now: datetime | None = None,
) -> Subscription:
    """Validate and apply a subscription status transition, recording the event."""
    from_status = subscription.status
    assert_transition(from_status, to_status)
    moment = now or utcnow()

    subscription.status = to_status
    subscription.updated_at = moment
    if to_status == "active" and subscription.activated_at is None:
        subscription.activated_at = moment
    if to_status == "suspended":
        subscription.suspended_at = moment

    event = SubscriptionEvent(
        subscription_id=subscription.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        reason=reason,
        payload=payload,
        created_at=moment,
    )
    session.add(event)
    return subscription

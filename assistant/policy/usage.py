"""Usage counters for rate limits: how many times an action already ran, from our own records.

Where this sits: ``policy`` package. ``policy.executor.ActionExecutor`` calls these helpers before
every policy check and puts the result in ``context["usage"]``, which ``PolicyEngine`` reads for
``rate_limit`` (contracts §4.6). Read-only queries on ``asst.action_records`` and
``asst.conversations``.

Key ideas:
- The assistant counts what it did itself: only rows with ``executed=True`` count, so a denied
  or unconfirmed attempt never uses up the allowance.
- The executor fills the counters, not the caller: a mode handler cannot forget (or fake) them.
- "Today" is the UTC calendar day, the same clock ``ActionRecord.created_at`` uses.

Read next: ``policy/executor.py``, ``policy/engine.py``.
See: docs/contracts.md §4.6.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from core_common.db import session_scope
from core_common.models import ActionRecord, Conversation


def _executed_in_conversation(session: Session, conversation_id: str, action_name: str | None) -> int:
    stmt = select(func.count(ActionRecord.id)).where(
        ActionRecord.conversation_id == conversation_id, ActionRecord.executed.is_(True)
    )
    if action_name is not None:
        stmt = stmt.where(ActionRecord.action_name == action_name)
    return int(session.scalar(stmt) or 0)


def count_executed_actions(session_factory: sessionmaker, conversation_id: str) -> int:
    """Actions of any kind that actually ran in one conversation (for the per-conversation cap)."""
    with session_scope(session_factory) as session:
        return _executed_in_conversation(session, conversation_id, None)


def action_usage(
    session_factory: sessionmaker,
    *,
    conversation_id: str,
    customer_no: str | None,
    action_name: str,
    now: dt.datetime | None = None,
) -> dict[str, int]:
    """Counters for ``rate_limit``: ``per_conversation`` and ``per_customer_per_day``.

    ``per_customer_per_day`` counts executions since 00:00 UTC across every conversation of
    ``customer_no`` plus the current one (which may not be linked to the customer yet on its
    first turn). With no known customer it falls back to the current conversation's count.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    day_start = now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope(session_factory) as session:
        per_conversation = _executed_in_conversation(session, conversation_id, action_name)
        if not customer_no:
            return {"per_conversation": per_conversation, "per_customer_per_day": per_conversation}
        customer_conversations = select(Conversation.conversation_id).where(
            Conversation.customer_no == customer_no
        )
        per_day = session.scalar(
            select(func.count(ActionRecord.id)).where(
                ActionRecord.action_name == action_name,
                ActionRecord.executed.is_(True),
                ActionRecord.created_at >= day_start,
                or_(
                    ActionRecord.conversation_id == conversation_id,
                    ActionRecord.conversation_id.in_(customer_conversations),
                ),
            )
        )
    return {"per_conversation": per_conversation, "per_customer_per_day": int(per_day or 0)}

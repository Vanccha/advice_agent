"""Conversation persistence: load/save the typed state and append chat messages.

Where this sits: the only module in `modes/` that reads or writes `asst.conversations` and
`asst.messages`. Used by `modes.orchestrator`, `modes.approvals` and `modes.events`.

Key ideas:
- One place owns the database shape; everything else works with `ConversationState`.
- Each operation is its own short transaction (`session_scope`), so a failure later in a
  turn never leaves a half-written conversation row.
- Messages are stored already masked (`masked=True`): PII never reaches storage.

Read next: modes/state.py, modes/orchestrator.py.

See: docs/contracts.md §1.5.
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from core_common.db import session_scope
from core_common.models import Conversation, Message
from modes.state import ConversationState


class ConversationStore:
    def __init__(self, session_factory: sessionmaker, tenant: str) -> None:
        self._session_factory = session_factory
        self._tenant = tenant

    def load(self, conversation_id: str) -> tuple[ConversationState, str | None]:
        """Return the state and the customer number stored on the row, creating the row
        for a new conversation so later saves always find it."""
        with session_scope(self._session_factory) as session:
            conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
            if conv is None:
                conv = Conversation(
                    conversation_id=conversation_id,
                    tenant=self._tenant,
                    mode="ROUTER",
                    state={},
                )
                session.add(conv)
                session.flush()
            return ConversationState.from_stored(conv.state), conv.customer_no

    def save(
        self, conversation_id: str, state: ConversationState, customer_no: str | None
    ) -> None:
        with session_scope(self._session_factory) as session:
            conv = session.query(Conversation).filter_by(conversation_id=conversation_id).first()
            if conv is None:
                conv = Conversation(conversation_id=conversation_id, tenant=self._tenant)
                session.add(conv)
            conv.state = state.to_stored()
            # Mirrored into its own column so the mode can be queried without parsing JSON.
            conv.mode = state.mode.value
            if customer_no:
                conv.customer_no = customer_no
                conv.masked_customer_ref = customer_no

    def append_message(self, conversation_id: str, role: str, content: str, mode: str) -> None:
        with session_scope(self._session_factory) as session:
            session.add(
                Message(
                    conversation_id=conversation_id, role=role, content=content, mode=mode, masked=True
                )
            )

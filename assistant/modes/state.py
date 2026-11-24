"""Typed conversation state: everything the assistant remembers between two turns.

Where this sits: persisted by `modes.conversation_store` in the JSON column
`asst.conversations.state`; loaded at the start of every turn, mutated by the dispatcher
(`modes.dispatch`) and the mode handlers (`modes.investigation`), saved at the end.

Key ideas:
- One typed model instead of a loose dict: a misspelt key becomes an AttributeError at
  once instead of a silent `None` three turns later.
- Backward compatible by construction: rows written by older versions load fine because
  unknown keys are ignored and missing keys fall back to their defaults.
- The state holds only masked, non-secret data (history is already PII-masked).

Read next: modes/conversation_store.py, modes/dispatch.py, modes/investigation.py.

See: docs/contracts.md §4.3.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core_common.types import Diagnosis, Mode

# Only the most recent turns are kept: the history feeds model calls, so an unbounded list
# would grow every prompt (cost, latency, and more text to mask).
HISTORY_CAP = 16


class ConversationState(BaseModel):
    """All per-conversation memory, with defaults that mean "a brand new conversation"."""

    # `ignore` is what makes old rows loadable: a key we no longer use is simply dropped.
    model_config = ConfigDict(extra="ignore")

    mode: Mode = Field(
        default=Mode.ROUTER,
        description="The mode the next customer message is handled in.",
    )
    customer_no: str | None = Field(
        default=None,
        description="The signed-in customer, remembered so later turns need not repeat it.",
    )
    history: list[dict[str, str]] = Field(
        default_factory=list,
        description="Masked turns, oldest first: {'role': 'user'|'assistant', 'content': str}.",
    )
    diagnosis: Diagnosis | None = Field(
        default=None,
        description="Result of the last diagnostic checklist; reused while an investigation "
        "is waiting for the customer's answer, so the checklist is not re-run each turn.",
    )

    # --- Advisory -------------------------------------------------------------------
    advisory_profile: dict[str, Any] = Field(
        default_factory=dict,
        description="What the customer has told us so far, as an AdvisoryProfile JSON dict. "
        "An empty dict means advisory has not started yet.",
    )
    advisory_awaiting_field: str | None = Field(
        default=None,
        description="The profile field the last question asked about; the next message answers it.",
    )
    advisory_questions_asked: int = Field(
        default=0, description="Questions asked in this advisory round (bounded by policy limits)."
    )
    advisory_first_turn: bool = Field(
        default=False,
        description="True only for the turn on which the router just handed over to advisory: "
        "that message is the original request, not an answer.",
    )

    # --- Investigation --------------------------------------------------------------
    investigation_rounds: int = Field(
        default=0,
        description="Clarification questions asked so far; above zero means the next message "
        "continues an open investigation.",
    )
    investigation_prior: dict[str, Any] = Field(
        default_factory=dict,
        description="What earlier rounds learned: {'questions': [...], 'summary': '...'}. "
        "Fed back to the model so it never asks the same question twice.",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _missing_mode_means_router(cls, value: Any) -> Any:
        # Old rows stored `mode: null` before the first turn finished.
        return Mode.ROUTER if value is None else value

    @field_validator("history", mode="before")
    @classmethod
    def _null_history_means_empty(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("advisory_profile", "investigation_prior", mode="before")
    @classmethod
    def _null_mapping_means_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("diagnosis", mode="before")
    @classmethod
    def _empty_diagnosis_means_none(cls, value: Any) -> Any:
        # An empty dict is not a Diagnosis (root_cause and scope are required).
        return value or None

    # --- Persistence ----------------------------------------------------------------
    @classmethod
    def from_stored(cls, raw: dict[str, Any] | None) -> ConversationState:
        """Build the state from the JSON column (None or {} for a new conversation)."""
        return cls.model_validate(raw or {})

    def to_stored(self) -> dict[str, Any]:
        """The JSON-safe dict written back to the column."""
        return self.model_dump(mode="json")

    # --- Investigation bookkeeping --------------------------------------------------
    def has_open_investigation(self) -> bool:
        """True while a clarification is pending, i.e. the next message continues it."""
        return self.investigation_rounds > 0

    def clear_investigation(self) -> None:
        """Forget clarification progress (a cause was found, or the turn ended in a handover)."""
        self.investigation_rounds = 0
        self.investigation_prior = {}

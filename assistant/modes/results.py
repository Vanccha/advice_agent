"""Result types of one turn: what the API returns and what a mode step hands upwards.

Where this sits: imported by `modes.orchestrator` (re-exports `TurnResult`), `modes.dispatch`,
`modes.approvals` and `modes.events`. No logic, only shapes.

Key ideas:
- Two layers on purpose: `StepOutcome` is internal (what the dispatcher produced);
  `TurnResult` is the public response contract, so the API shape cannot drift by accident.

Read next: modes/orchestrator.py, modes/dispatch.py.

See: docs/contracts.md §4.2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TurnResult(BaseModel):
    """contracts §4.2 response shape for `POST /api/chat` / `GET /api/chat/stream`."""

    model_config = ConfigDict(frozen=False)

    conversation_id: str
    mode: str
    reply_en: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    diagnosis: dict[str, Any] | None = None


@dataclass
class StepOutcome:
    """What the dispatcher produced for one turn, before it is wrapped into a `TurnResult`."""

    reply_en: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    diagnosis: dict[str, Any] | None = None

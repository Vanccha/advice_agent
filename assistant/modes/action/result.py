"""What an action step hands back to the dispatcher, plus the one helper every outcome shares.

Where this sits: `modes.action`, lowest layer. Every sibling module returns an
`ActionStepResult`; keeping it here lets them import it without importing each other.

Key ideas:
- The result says what to tell the customer (`reply_en`), where the conversation goes next
  (`next_mode`) and what happened (`actions`, `ticket_key`, approval fields) for the API.

Read next: modes/action/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core_common.types import Department, Mode
from modes.context import TurnContext


@dataclass
class ActionStepResult:
    """The outcome of one ACTION step: the reply, the next mode and what was done."""

    reply_en: str
    next_mode: Mode
    ticket_key: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)


def department_en(ctx: TurnContext, department: Department | str | None) -> str:
    """The department's display name for customer wording; the raw code if the tenant has none."""
    if department is None:
        return "the relevant team"
    code = department.value if isinstance(department, Department) else str(department)
    dept_cfg = ctx.tenant_config.departments.get(code)
    return dept_cfg.display_name_en if dept_cfg else code

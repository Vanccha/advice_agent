from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.sla import sla_due_at


@pytest.mark.parametrize(
    "priority,hours",
    [("URGENT", 2), ("HIGH", 8), ("NORMAL", 24), ("LOW", 72)],
)
def test_sla_due_at_matches_contract(priority: str, hours: int) -> None:
    created = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert sla_due_at(priority, created) == created + timedelta(hours=hours)


def test_sla_due_at_rejects_unknown_priority() -> None:
    with pytest.raises(ValueError):
        sla_due_at("SUPER_URGENT", datetime.now(timezone.utc))

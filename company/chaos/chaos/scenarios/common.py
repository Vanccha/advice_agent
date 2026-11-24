"""Shared result types and lookup helpers for every scenario.

Where this sits: imported by each scenario module and by `cli.py`.

Key ideas:
- Every scenario returns the same shapes (`ScenarioOutcome`, `StatusEntry`,
  `ResetOutcome`), so the CLI can print any of them without special cases.
- `expected_behavior` documents what the support tooling should do about the
  fault, which doubles as the acceptance criterion for rehearsals.

Read next: `stuck_provisioning.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass
class ScenarioOutcome:
    """What applying a scenario did (or, in a dry run, would do), and what behaviour to expect."""
    scenario: str
    dry_run: bool
    picked: dict[str, Any]
    changes: list[str]
    expected_behavior: str
    records: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusEntry:
    """Whether one scenario's fault is currently active, with its records."""
    scenario: str
    active: bool
    records: list[dict[str, Any]]


@dataclass
class ResetOutcome:
    """What resetting a scenario undid."""
    scenario: str
    changes: list[str]
    records: dict[str, Any] = field(default_factory=dict)


def customer_row(conn: Connection, customer_no: str) -> dict:
    """Look a customer up by number or raise `ChaosError`."""
    cust = conn.execute(
        text(
            "SELECT c.id AS customer_id, c.customer_no, c.region_code "
            "FROM core.customers c WHERE c.customer_no = :no"
        ),
        {"no": customer_no},
    ).mappings().first()
    if cust is None:
        raise ChaosError(f"Customer '{customer_no}' not found.")
    return dict(cust)


def latest_subscription(conn: Connection, customer_id: int) -> dict:
    """The customer's most recent subscription or raise `ChaosError`."""
    sub = conn.execute(
        text(
            "SELECT id, status FROM core.subscriptions WHERE customer_id = :cid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"cid": customer_id},
    ).mappings().first()
    if sub is None:
        raise ChaosError("Customer has no subscription.")
    return dict(sub)

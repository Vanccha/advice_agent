"""Chaos scenarios: one module per injected failure, plus this registry.

Where this sits: the `chaos.cli` commands import this package as `sc` and call
each scenario's `pick`/`apply`/`detect`/`reset` functions; the tests do the same.

Key ideas:
- Chaos / failure injection: each scenario puts the stack into a state that is
  indistinguishable from a real incident, by using the company's own APIs and
  tables the way an operator would.
- Every scenario has four verbs: pick a victim (read only), apply the fault,
  detect whether it is active, reset it.
- Every record a scenario touches is traceable (a `chaos` actor in the event
  trail), so a reset can find and undo exactly what it did.

Registry (this file re-exports every public name, so callers write
`sc.apply_regional_outage(...)` and need not know which module defines it):

- common               shared result types and lookup helpers
- stuck_provisioning   a provisioning job frozen mid-run
- paid_not_active      a paid subscription stranded before provisioning
- regional_outage      a critical regional incident with affected customers
- double_charge        two identical succeeded charges minutes apart
- missed_installation  an installation visit that did not happen
- payment_down         the payment gateway switched into outage

Read next: `common.py`, then any one scenario module.
"""
from __future__ import annotations

from chaos.scenarios.common import ResetOutcome, ScenarioOutcome, StatusEntry
from chaos.scenarios.double_charge import (
    apply_double_charge,
    detect_double_charge,
    pick_double_charge,
    reset_double_charge,
)
from chaos.scenarios.missed_installation import (
    apply_missed_installation,
    detect_missed_installation,
    pick_missed_installation,
    reset_missed_installation,
)
from chaos.scenarios.paid_not_active import (
    apply_paid_not_active,
    detect_paid_not_active,
    find_paid_not_active_candidate,
    reset_paid_not_active,
)
from chaos.scenarios.payment_down import apply_payment_down, detect_payment_down, reset_payment_down
from chaos.scenarios.regional_outage import (
    apply_regional_outage,
    detect_regional_outage,
    pick_regional_outage,
    reset_regional_outage,
)
from chaos.scenarios.stuck_provisioning import (
    FRESH_SIGNUP_PACKAGE,
    apply_stuck_provisioning,
    create_fresh_signup,
    detect_stuck_provisioning,
    ensure_candidate,
    find_promotable_subscription,
    find_stuck_provisioning_candidate,
    promote_to_payment_received,
    reset_stuck_provisioning,
)

__all__ = [
    "FRESH_SIGNUP_PACKAGE",
    "ResetOutcome",
    "ScenarioOutcome",
    "StatusEntry",
    "apply_double_charge",
    "apply_missed_installation",
    "apply_paid_not_active",
    "apply_payment_down",
    "apply_regional_outage",
    "apply_stuck_provisioning",
    "create_fresh_signup",
    "detect_double_charge",
    "detect_missed_installation",
    "detect_paid_not_active",
    "detect_payment_down",
    "detect_regional_outage",
    "detect_stuck_provisioning",
    "ensure_candidate",
    "find_paid_not_active_candidate",
    "find_promotable_subscription",
    "find_stuck_provisioning_candidate",
    "pick_double_charge",
    "pick_missed_installation",
    "pick_regional_outage",
    "promote_to_payment_received",
    "reset_double_charge",
    "reset_missed_installation",
    "reset_paid_not_active",
    "reset_payment_down",
    "reset_regional_outage",
    "reset_stuck_provisioning",
]

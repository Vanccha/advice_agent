"""DIAGNOSTIC mode, first stage: the fixed checklist that names a known root cause.

Where this sits: called by `modes.dispatch` for a problem report. Walks the checklist from
``routing.yaml: diagnostic_checklist`` (customer, subscription, payment, provisioning,
installation, regional incidents, service health) through the gateway's read-only tools,
stopping at the first established root cause. Its `Diagnosis` feeds `modes.action`; when
nothing matches, `modes.investigation` takes over.

Key ideas:
- Deterministic first: cheap, explainable rules run before any model is involved.
- Evidence hygiene: only non-PII fields go into `Diagnosis.evidence`.
- Root causes are `core_common.types.RootCause` members, not loose strings.

Public entry point: `run_diagnosis`.

Read next, in this order: `checklist.py` (the walk), `payment_checks.py` (reading payment
and gateway-health payloads), then modes/investigation and modes/action.

Import as: ``from modes.diagnostic import run_diagnosis``.
See: docs/contracts.md §4.3, §5.
"""
from __future__ import annotations

from modes.diagnostic.checklist import run_diagnosis

__all__ = ["run_diagnosis"]

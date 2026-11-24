"""ADVISORY mode: ask a few fixed questions, then recommend packages deterministically.

Where this sits: entered from the router; `modes.dispatch` calls `handle_turn` once per
customer message. Fills an `AdvisoryProfile`, then calls
`recommendation.engine.recommend_packages` on the live catalogue (via the gateway) and
asks the LLM provider only to narrate the result.

Key ideas:
- The LLM never chooses, orders or prices a package: only the scoring engine does
  (contracts §4.3/§4.4). Its wording is validated against the engine's offers and falls
  back to a fixed template on any mismatch or error.
- Answers are read by a model plus offline regex fallbacks, through one plausibility
  check, so the mode still works without an LLM.
- Bounded: a fixed question budget, then it recommends with what it has.

Public entry points: `handle_turn`, `AdvisoryStepResult`.

Read next, in this order: `parsing.py` (regex fallbacks and plausibility), `extraction.py`
(model reading of fields), `narration.py` (wording and its validation), `flow.py` (the
question flow that ties them together).

Import as: ``from modes.advisory import handle_turn, AdvisoryStepResult``.
See: docs/contracts.md §4.3, §4.4.
"""
from __future__ import annotations

from modes.advisory.flow import AdvisoryStepResult, handle_turn

__all__ = ["AdvisoryStepResult", "handle_turn"]

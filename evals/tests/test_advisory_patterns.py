"""Unit tests for the `reasons_from_config_only` invariant's regex builder
(`evals/advisory_suite.py`). Pure string/regex logic — no live stack needed, but does
import `evals` (and therefore puts `assistant/` on `sys.path`), so it still needs to run
where `assistant/`'s own dependencies (pydantic, …) are installed, i.e. inside the
test-runner image, exactly like the rest of this harness.
"""
from __future__ import annotations

from evals.advisory_suite import _matches_any, _reason_patterns

_TEMPLATES = {
    "speed_ok": "{down_mbps} Mbps is plenty for the {device_count} devices in your home.",
    "budget_ok": "At {monthly_price_gbp} a month, it is within your budget.",
    "no_commitment": "No minimum term — you can leave whenever you like.",
}


def test_reason_matching_a_template_with_placeholders():
    patterns = _reason_patterns(_TEMPLATES)
    assert _matches_any("200 Mbps is plenty for the 8 devices in your home.", patterns)


def test_reason_matching_a_template_without_placeholders():
    patterns = _reason_patterns(_TEMPLATES)
    assert _matches_any("No minimum term — you can leave whenever you like.", patterns)


def test_reason_not_matching_free_text():
    patterns = _reason_patterns(_TEMPLATES)
    assert not _matches_any("This package is brilliant, buy it now!", patterns)


def test_reason_placeholder_does_not_match_across_templates():
    # A reason using one template's literal frame but a nonsense fill is still an exact
    # structural match (this only proves the string *shape* came from the table, not that
    # a particular number is semantically right) — documented in evals/README.md.
    patterns = _reason_patterns(_TEMPLATES)
    assert _matches_any("At asdf a month, it is within your budget.", patterns)

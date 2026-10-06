"""Unit tests for the `reasons_from_config_only` invariant's regex builder
(`evals/advisory_suite.py`). Pure string/regex logic — no live stack needed, but does
import `evals` (and therefore puts `assistant/` on `sys.path`), so it still needs to run
where `assistant/`'s own dependencies (pydantic, …) are installed, i.e. inside the
test-runner image, exactly like the rest of this harness.
"""
from __future__ import annotations

from evals.advisory_suite import _matches_any, _reason_patterns

_TEMPLATES = {
    "speed_ok": "Ev içindeki {device_count} cihaz için {down_mbps} Mbps rahat yeter.",
    "budget_ok": "Aylık {monthly_price_try} TL bütçenizin içinde.",
    "no_commitment": "Taahhüt yok, istediğiniz zaman çıkabilirsiniz.",
}


def test_reason_matching_a_template_with_placeholders():
    patterns = _reason_patterns(_TEMPLATES)
    assert _matches_any("Ev içindeki 8 cihaz için 200 Mbps rahat yeter.", patterns)


def test_reason_matching_a_template_without_placeholders():
    patterns = _reason_patterns(_TEMPLATES)
    assert _matches_any("Taahhüt yok, istediğiniz zaman çıkabilirsiniz.", patterns)


def test_reason_not_matching_free_text():
    patterns = _reason_patterns(_TEMPLATES)
    assert not _matches_any("Bu paket harika, hemen alın!", patterns)


def test_reason_placeholder_does_not_match_across_templates():
    # A reason using one template's literal frame but a nonsense fill is still an exact
    # structural match (this only proves the string *shape* came from the table, not that
    # a particular number is semantically right) — documented in evals/README.md.
    patterns = _reason_patterns(_TEMPLATES)
    assert _matches_any("Aylık asdf TL bütçenizin içinde.", patterns)

"""Unit tests for the pure, dependency-free pieces of the eval harness. No live stack, no
chaos CLI, no docker — these run with plain `pytest evals/tests` (also inside
`make test`'s test-runner image).
"""
from __future__ import annotations

from evals.checks import CaseResult, contains_any, contains_none, error_case


def test_contains_any_is_case_insensitive():
    assert contains_any("Faturalama ekibine ilettim", ["faturalama"])
    assert not contains_any("Faturalama ekibine ilettim", ["teknik altyapı"])


def test_contains_none_is_inverse_of_contains_any():
    assert contains_none("herşey yolunda", ["arıza", "sorun"])
    assert not contains_none("bir arıza tespit edildi", ["arıza"])


def test_contains_any_handles_none_text():
    assert not contains_any(None, ["x"])
    assert contains_none(None, ["x"])


def test_case_result_finalize_pass():
    case = CaseResult(suite="scenarios", case_id="x")
    case.add("a", True)
    case.add("b", True)
    case.finalize()
    assert case.status == "PASS"


def test_case_result_finalize_fail_on_any_failed_check():
    case = CaseResult(suite="scenarios", case_id="x")
    case.add("a", True)
    case.add("b", False, expected=1, actual=2)
    case.finalize()
    assert case.status == "FAIL"


def test_case_result_finalize_never_overwrites_error():
    case = CaseResult(suite="scenarios", case_id="x", status="ERROR", message="boom")
    case.add("a", True)
    case.finalize()
    assert case.status == "ERROR"


def test_error_case_helper():
    case = error_case("scenarios", "x", "polite", "adapter unavailable")
    assert case.status == "ERROR"
    assert case.message == "adapter unavailable"
    assert case.checks == []


def test_full_id_includes_variant_only_when_present():
    assert CaseResult(suite="scenarios", case_id="x").full_id == "x"
    assert CaseResult(suite="scenarios", case_id="x", variant="polite").full_id == "x[polite]"

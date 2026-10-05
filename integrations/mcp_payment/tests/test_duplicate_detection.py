"""`detect_duplicate_charges` is the piece the assistant's double-charge
diagnosis depends on (docs/contracts.md §3), so its core logic is tested
directly against planted data — no network, no DB.
"""
from __future__ import annotations

from app.duplicates import find_duplicate_groups


def _charge(ref: str, amount: float, created_at: str, status: str = "succeeded") -> dict:
    return {
        "charge_ref": ref,
        "amount_try": amount,
        "created_at": created_at,
        "status": status,
    }


def test_finds_a_planted_duplicate_pair() -> None:
    charges = [
        _charge("ch_1", 459.00, "2026-10-05T12:00:00Z"),
        _charge("ch_2", 459.00, "2026-10-05T12:04:00Z"),  # 4 minutes later, same amount
    ]
    groups = find_duplicate_groups(charges, window_minutes=60)
    assert len(groups) == 1
    assert groups[0]["amount_try"] == 459.00
    assert groups[0]["charge_refs"] == ["ch_1", "ch_2"]
    assert groups[0]["count"] == 2


def test_ignores_two_different_amounts() -> None:
    charges = [
        _charge("ch_1", 459.00, "2026-10-05T12:00:00Z"),
        _charge("ch_2", 629.00, "2026-10-05T12:04:00Z"),
    ]
    groups = find_duplicate_groups(charges, window_minutes=60)
    assert groups == []


def test_ignores_a_single_charge() -> None:
    charges = [_charge("ch_1", 459.00, "2026-10-05T12:00:00Z")]
    assert find_duplicate_groups(charges, window_minutes=60) == []


def test_ignores_failed_charges() -> None:
    charges = [
        _charge("ch_1", 459.00, "2026-10-05T12:00:00Z", status="failed"),
        _charge("ch_2", 459.00, "2026-10-05T12:04:00Z", status="failed"),
    ]
    assert find_duplicate_groups(charges, window_minutes=60) == []


def test_same_amount_outside_window_is_not_a_duplicate() -> None:
    charges = [
        _charge("ch_1", 459.00, "2026-10-05T12:00:00Z"),
        _charge("ch_2", 459.00, "2026-10-05T14:00:00Z"),  # 2 hours later
    ]
    groups = find_duplicate_groups(charges, window_minutes=60)
    assert groups == []


def test_three_charges_same_amount_form_one_group() -> None:
    charges = [
        _charge("ch_1", 100.00, "2026-10-05T12:00:00Z"),
        _charge("ch_2", 100.00, "2026-10-05T12:10:00Z"),
        _charge("ch_3", 100.00, "2026-10-05T12:20:00Z"),
    ]
    groups = find_duplicate_groups(charges, window_minutes=60)
    assert len(groups) == 1
    assert groups[0]["count"] == 3
    assert groups[0]["charge_refs"] == ["ch_1", "ch_2", "ch_3"]


def test_mixed_amounts_and_duplicates_in_one_batch() -> None:
    charges = [
        _charge("ch_1", 459.00, "2026-10-05T12:00:00Z"),
        _charge("ch_2", 459.00, "2026-10-05T12:04:00Z"),  # duplicate of ch_1
        _charge("ch_3", 349.00, "2026-10-05T12:05:00Z"),  # different amount, lone
        _charge("ch_4", 629.00, "2026-10-05T09:00:00Z", status="failed"),  # failed, ignored
    ]
    groups = find_duplicate_groups(charges, window_minutes=60)
    assert len(groups) == 1
    assert groups[0]["amount_try"] == 459.00
    assert groups[0]["charge_refs"] == ["ch_1", "ch_2"]


def test_window_boundary_is_inclusive() -> None:
    charges = [
        _charge("ch_1", 100.00, "2026-10-05T12:00:00Z"),
        _charge("ch_2", 100.00, "2026-10-05T13:00:00Z"),  # exactly 60 minutes later
    ]
    groups = find_duplicate_groups(charges, window_minutes=60)
    assert len(groups) == 1
    assert groups[0]["count"] == 2

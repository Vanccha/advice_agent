"""Shared helpers for reading a tool call's payload (contracts §3).

``ToolCallOutcome.data`` holds the adapter's **own** ``ToolResult`` envelope
(``{ok, data, error, source}``) — a second layer underneath the gateway's own
``ToolCallOutcome`` — and that inner ``data`` is in turn often a dict wrapping a single
named collection (e.g. ``{"customers": [...]}``, ``{"subscriptions": [...]}``) rather than
the bare record. `FakeGateway`-backed tests hand back already-flat dicts (or a bare
``{"items": [...]}``), so every helper here degrades gracefully when there is no adapter
envelope to unwrap, which keeps both shapes working unchanged.

Import as: ``from modes.tool_data import unwrap, first_record, as_list``.
"""
from __future__ import annotations

from typing import Any


def unwrap(outcome: Any) -> Any:
    """Gateway-level failure (``outcome.ok is False``) or an inner adapter-level failure
    (``outcome.data["ok"] is False``) both collapse to ``None``. Otherwise returns the
    adapter's own ``data`` payload, or `outcome.data` unchanged if it was never wrapped."""
    if outcome is None or not getattr(outcome, "ok", False):
        return None
    raw = outcome.data
    if isinstance(raw, dict) and {"ok", "data", "error"} <= raw.keys():
        if not raw.get("ok"):
            return None
        return raw.get("data")
    return raw


def _first_list_value(payload: dict[str, Any]) -> list[Any] | None:
    for value in payload.values():
        if isinstance(value, list):
            return value
    return None


def first_record(outcome: Any) -> dict[str, Any] | None:
    """The first record of a tool's payload, whether that payload is already a flat
    record, a bare list, or a dict wrapping one named list (``{"customers": [...]}``,
    ``{"items": [...]}``, ...)."""
    payload = unwrap(outcome)
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        collection = _first_list_value(payload)
        if collection is not None:
            return collection[0] if collection and isinstance(collection[0], dict) else None
        return payload
    return None


def as_list(outcome: Any) -> list[dict[str, Any]]:
    """Every record of a tool's payload, in the same three shapes `first_record` accepts."""
    payload = unwrap(outcome)
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        collection = _first_list_value(payload)
        if collection is not None:
            return collection
        return [payload] if payload else []
    return []


def raw_payload(outcome: Any) -> Any:
    """The unwrapped payload with no further shape assumptions — for tools whose result
    is neither a single record nor a homogeneous collection (e.g. ``get_service_health``,
    ``detect_duplicate_charges``)."""
    return unwrap(outcome)

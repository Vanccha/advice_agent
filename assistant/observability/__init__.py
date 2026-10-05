"""Tracing + redaction wrapper around the self-hosted Langfuse stack (contracts §4.9).

Public surface (import as ``from observability.tracing import ...`` /
``from observability.redaction import ...`` / ``from observability.fallback import ...``):

- ``init_tracing(settings) -> TracingHandle`` — call once at process startup.
- ``trace_turn(...)`` — context manager wrapping one conversation turn.
- ``record_event`` / ``record_decision`` / ``record_tool_call`` — span-level helpers.

Everything here is fail-open: a tracing problem (disabled, unreachable, bad payload) must
never raise into the caller's turn. See ``docs/observability.md`` for the operator's view.
"""

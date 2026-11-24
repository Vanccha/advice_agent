"""Tracing for the assistant: Langfuse when enabled, a local file otherwise, never a crash.

Public entry points: ``observability.tracing`` (``init_tracing``, ``trace_turn``,
``record_event``, ``record_decision``, ``record_tool_call``), ``observability.redaction``
(``redact_payload``, ``redact_metadata``) and ``observability.fallback`` (the JSONL sink).
Everything is fail-open and everything emitted is masked first. See docs/observability.md.
"""

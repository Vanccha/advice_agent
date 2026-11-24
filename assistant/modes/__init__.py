"""The conversation engine: an explicit state machine with one handler per mode (the large ones are packages).

Where this sits: the heart of the assistant. `api/` calls `modes.orchestrator.Orchestrator`;
the orchestrator drives `modes.dispatch`, which runs the handlers
(`router`, `advisory`, `diagnostic`, `investigation`, `status_query`, `action`) under the
transition table in `modes.machine`, with typed memory in `modes.state`.

Key ideas:
- Explicit state machine: modes and legal transitions are data, not hidden control flow.
- Every customer-visible fixed sentence lives in `modes.replies`.

Read next: modes/orchestrator.py, modes/machine.py.

Import the facade as: ``from modes.orchestrator import Orchestrator, TurnResult``.
"""
from __future__ import annotations

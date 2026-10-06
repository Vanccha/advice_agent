"""Evaluation harness for the AI support assistant (contracts §7, CLAUDE.md `evals/`).

Two suites, both driven against the *real* `Orchestrator` (assistant/modes/orchestrator.py)
with a `ScriptedProvider` (no API key needed) and a live `ToolGateway` pointed at the
tenant's MCP adapters — never a `FakeGateway`, so a scenario run exercises the actual
company stack exactly like a live chat turn would.

`evals/` is allowed to import from `assistant/` (it is the product's own harness) but must
never import `company` or `integrations` directly (CLAUDE.md hard rule); the chaos
scenarios are injected by shelling out to the company's own `chaos.cli` subprocess
(`evals/chaos_cli.py`), never by importing `company.chaos` in-process.

This package is executed as `python -m evals.run` from `/workspace` inside the
`test-runner` container (see `make eval`), which does **not** set `PYTHONPATH` for us —
`assistant/` (so `core_common`, `modes`, `llm`, ... import as top-level packages, exactly
as the assistant image itself resolves them) is added to `sys.path` right here, in
`__init__.py`, so every submodule of this package can `import core_common`/`modes`/... at
module load time regardless of which submodule happens to be imported first.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSISTANT_DIR = REPO_ROOT / "assistant"

if str(ASSISTANT_DIR) not in sys.path:
    sys.path.insert(0, str(ASSISTANT_DIR))

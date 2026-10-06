"""Subprocess wrapper around the company's own failure-injection tool
(``company/chaos/chaos/cli.py``, contracts §5).

This is the *only* way this harness touches ``company/`` code: never an in-process
``import chaos...`` (CLAUDE.md hard rule 1 — nothing under ``assistant/`` or ``evals/`` may
import ``company``), always a subprocess, exactly as a human operator would run
``make chaos SCENARIO=...``.

``docker compose run --rm chaos python -m chaos.cli ...`` cannot be used *from inside*
the ``test-runner`` container (no Docker socket is mounted there), so instead we run the
chaos package directly as a subprocess of the eval runner: the whole repository is bind
-mounted at the container's working directory (``./:/workspace``), so ``company/`` and
``company/chaos/`` are on disk right next to ``evals/`` — we just point a *child* process's
``PYTHONPATH`` at them (never our own ``sys.path``, which stays clean of ``company``) and
let it import ``chaos.cli`` and ``shared.*`` the same way the ``chaos`` container's own
entrypoint does. The chaos CLI then talks to the live company database/APIs using the
connection env vars already present in the test-runner's environment (contracts §8:
``COMPANY_DB_*``, ``CORE_API_KEY_CRM``, ``PSP_API_KEY``, ``CORE_API_BASE_URL``, ...).

Import as: ``from evals.chaos_cli import inject, reset, status, ChaosCliError``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
_COMPANY_DIR = REPO_ROOT / "company"
_CHAOS_DIR = _COMPANY_DIR / "chaos"

DEFAULT_TIMEOUT = 60.0


class ChaosCliError(RuntimeError):
    """Raised when the chaos CLI exits non-zero, or prints something that isn't the JSON
    it promises with ``--json`` — either way this must be loud, never swallowed into a
    silent "nothing happened"."""


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    prefix = f"{_COMPANY_DIR}:{_CHAOS_DIR}"
    env["PYTHONPATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def _run(args: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    proc = subprocess.run(
        ["python", "-m", "chaos.cli", *args, "--json"],
        cwd=str(REPO_ROOT),
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = proc.stdout.strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError as exc:
        raise ChaosCliError(
            f"chaos CLI call {args!r} produced non-JSON output (exit={proc.returncode}): "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        ) from exc

    if proc.returncode != 0 or (isinstance(payload, dict) and "error" in payload):
        reason = payload.get("error") if isinstance(payload, dict) else None
        raise ChaosCliError(
            f"chaos CLI call {args!r} failed (exit={proc.returncode}): "
            f"{reason or proc.stderr.strip() or '(no error detail)'}"
        )
    return payload


def inject(scenario: str, *, customer: str | None = None, region: str | None = None) -> dict[str, Any]:
    """Run one chaos scenario live against the company stack (contracts §5 a-f).

    Returns the parsed ``ScenarioOutcome`` JSON: ``{"scenario", "dry_run", "picked",
    "changes", "expected_behavior", "records"}``. ``picked`` is the authoritative source
    for which customer/subscription/region/incident the scenario actually touched — the
    eval runner must read it from here, never guess or hardcode a customer number.
    """
    args = [scenario]
    if customer:
        args += ["--customer", customer]
    if region:
        args += ["--region", region]
    return _run(args)


def reset() -> dict[str, Any]:
    """Undo every injected chaos scenario and restore normal control flags (contracts §5:
    ``reset`` restores incidents/charges/credits/appointments/jobs/modems/PSP control)."""
    return _run(["reset"])


def status() -> dict[str, Any]:
    """Which chaos scenarios are currently active (diagnostic use only)."""
    return _run(["status"])

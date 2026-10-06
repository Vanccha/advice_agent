"""`python -m evals.run` — the eval harness entrypoint (`make eval`).

Runs the scenario suite (`evals/datasets/scenarios.yaml`) and the advisory suite
(`evals/datasets/advisory_profiles.yaml`) against the real `Orchestrator`, prints a
PASS/FAIL/ERROR summary, writes `evals/reports/<timestamp>-report.{md,json}`, and exits
non-zero unless every case passed (so `make eval` is CI-usable).

Importing the `evals` package (see `evals/__init__.py`) puts `assistant/` on `sys.path`
before anything in this module imports from `core_common`/`modes`/`llm`/... — required
because the Makefile's `make eval` runs this as `python -m evals.run` from `/workspace`
with no `PYTHONPATH` of its own.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import evals  # noqa: F401 - import side effect: puts assistant/ on sys.path
import yaml

from evals.advisory_suite import run_advisory_suite
from evals.chaos_cli import ChaosCliError
from evals.chaos_cli import reset as chaos_reset
from evals.checks import CaseResult, error_case
from evals.report import print_console_summary, write_reports
from evals.scenario_suite import run_scenario_suite
from evals.wiring import EvalHarness

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parent / "reports"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NetSwift AI support assistant eval harness")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_DIR, help="report output directory")
    parser.add_argument("--only", choices=["scenarios", "advisory"], default=None, help="run one suite only")
    parser.add_argument("--case", default=None, help="run a single case/profile id only")
    parser.add_argument("--no-chaos", action="store_true", help="skip chaos injection (quick advisory-only run)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    scenarios_doc = _load_yaml(DATASETS_DIR / "scenarios.yaml")
    advisory_doc = _load_yaml(DATASETS_DIR / "advisory_profiles.yaml")

    # Imported late (only once `evals/__init__.py` has fixed up sys.path).
    from core_common.config import load_tenant_config
    from evals.fixtures import build_decision_rules, build_intent_rules

    rules = build_intent_rules(scenarios_doc, advisory_doc)
    try:
        # Needs the tenant's routing.yaml (urgency_floor) — loaded once here, cached, and
        # loaded again (free, same cache) when EvalHarness builds the tenant config itself.
        rules += build_decision_rules(load_tenant_config().routing)
    except Exception as exc:  # noqa: BLE001 - reported below via the harness-startup error path
        print(f"WARNING: could not build decision-layer fixtures: {exc}", file=sys.stderr)

    try:
        harness = EvalHarness(rules)
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: could not build the eval harness: {exc}", file=sys.stderr)
        error = error_case("harness", "startup", None, str(exc))
        meta = {"provider_mode": "unknown", "tenant": "unknown", "commit": _git_commit()}
        print_console_summary([error], meta)
        write_reports([error], args.report, meta)
        return 1

    results: list[CaseResult] = []

    run_scenarios = args.only in (None, "scenarios")
    run_advisory = args.only in (None, "advisory")

    if run_scenarios:
        results.extend(
            run_scenario_suite(
                harness, scenarios_doc, only_case=args.case, no_chaos=args.no_chaos
            )
        )

    if run_advisory:
        results.extend(run_advisory_suite(harness, advisory_doc, only_case=args.case))

    # Leave chaos state reset when we finish, regardless of what happened above (hard
    # rule: "Leave the chaos state reset when you finish").
    if run_scenarios and not args.no_chaos:
        try:
            chaos_reset()
        except ChaosCliError as exc:
            results.append(error_case("scenarios", "final-reset", None, f"final chaos reset failed: {exc}"))

    meta = {
        "provider_mode": harness.settings.EVAL_MODE,
        "tenant": harness.tenant_config.tenant_name,
        "commit": _git_commit(),
    }

    print_console_summary(results, meta)
    md_path, json_path = write_reports(results, args.report, meta)
    print(f"\nReport written to: {md_path}")
    print(f"Machine-readable report: {json_path}")

    if not results:
        print("WARNING: no cases ran at all — check --only/--case filters", file=sys.stderr)
        return 1

    ok = all(r.status == "PASS" for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

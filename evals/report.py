"""Console summary + `evals/reports/<timestamp>-report.{md,json}` writer.

Import as: ``from evals.report import print_console_summary, write_reports``.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from evals.checks import CaseResult

STATUS_ICON = {"PASS": "PASS ", "FAIL": "FAIL ", "ERROR": "ERROR"}


def print_console_summary(results: list[CaseResult], meta: dict[str, Any]) -> None:
    print("=" * 88)
    print(f"Eval run — provider mode: {meta['provider_mode']} | tenant: {meta['tenant']} | commit: {meta['commit']}")
    print("=" * 88)

    by_suite: dict[str, list[CaseResult]] = {}
    for r in results:
        by_suite.setdefault(r.suite, []).append(r)

    for suite, suite_results in by_suite.items():
        print(f"\n--- {suite} ---")
        for r in suite_results:
            icon = STATUS_ICON.get(r.status, r.status)
            if r.status == "ERROR":
                print(f"  {icon}  {r.full_id:40s} {r.message}")
            elif r.status == "FAIL":
                failing = ", ".join(c.name for c in r.checks if not c.passed)
                print(f"  {icon}  {r.full_id:40s} failing: {failing}")
                for c in r.checks:
                    if not c.passed:
                        print(f"          - {c.name}: expected={c.expected!r} actual={c.actual!r}")
            else:
                print(f"  {icon}  {r.full_id:40s}")

        total = len(suite_results)
        passed = sum(1 for r in suite_results if r.status == "PASS")
        failed = sum(1 for r in suite_results if r.status == "FAIL")
        errored = sum(1 for r in suite_results if r.status == "ERROR")
        print(f"  -- {suite} totals: {passed}/{total} passed, {failed} failed, {errored} errored")

    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    errored = sum(1 for r in results if r.status == "ERROR")
    print("\n" + "=" * 88)
    print(f"OVERALL: {passed}/{total} passed, {failed} failed, {errored} errored")
    print("=" * 88)


def _write_markdown(results: list[CaseResult], meta: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append(f"# Eval report — {meta['timestamp']}")
    lines.append("")
    lines.append(f"- Provider mode: `{meta['provider_mode']}`")
    lines.append(f"- Tenant: `{meta['tenant']}`")
    lines.append(f"- Commit: `{meta['commit']}`")
    lines.append(f"- Overall: **{meta['passed']}/{meta['total']} passed**, {meta['failed']} failed, {meta['errored']} errored")
    lines.append("")

    by_suite: dict[str, list[CaseResult]] = {}
    for r in results:
        by_suite.setdefault(r.suite, []).append(r)

    for suite, suite_results in by_suite.items():
        lines.append(f"## {suite}")
        lines.append("")
        lines.append("| case | status | checks | detail |")
        lines.append("|---|---|---|---|")
        for r in suite_results:
            check_summary = ", ".join(
                f"{'✓' if c.passed else '✗'}{c.name}" for c in r.checks
            ) or "—"
            detail = r.message
            if r.status == "FAIL":
                detail = "; ".join(
                    f"{c.name}: expected={c.expected!r} actual={c.actual!r}" for c in r.checks if not c.passed
                )
            lines.append(f"| {r.full_id} | {r.status} | {check_summary} | {detail.replace(chr(10), ' ')} |")
        lines.append("")

    lines.append("## Failures in detail")
    lines.append("")
    any_failure = False
    for r in results:
        if r.status == "PASS":
            continue
        any_failure = True
        lines.append(f"### {r.suite}:{r.full_id} — {r.status}")
        if r.message:
            lines.append(f"- message: {r.message}")
        for c in r.checks:
            if not c.passed:
                lines.append(f"- **{c.name}**: expected `{c.expected!r}`, actual `{c.actual!r}` ({c.detail})")
        if r.reply_en:
            lines.append(f"- reply_en: `{r.reply_en}`")
        lines.append("")
    if not any_failure:
        lines.append("(none)")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_reports(results: list[CaseResult], report_dir: Path, meta: dict[str, Any]) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = dict(meta)
    meta["timestamp"] = timestamp
    meta["total"] = len(results)
    meta["passed"] = sum(1 for r in results if r.status == "PASS")
    meta["failed"] = sum(1 for r in results if r.status == "FAIL")
    meta["errored"] = sum(1 for r in results if r.status == "ERROR")

    json_path = report_dir / f"{timestamp}-report.json"
    md_path = report_dir / f"{timestamp}-report.md"

    json_path.write_text(
        json.dumps({"meta": meta, "results": [r.to_dict() for r in results]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_markdown(results, meta, md_path)

    return md_path, json_path

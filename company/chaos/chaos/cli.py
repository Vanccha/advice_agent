from __future__ import annotations

import json as json_module
from dataclasses import asdict
from typing import Any

import typer

from chaos import scenarios as sc
from chaos.db import core_engine, payment_engine
from chaos.errors import ChaosError
from chaos.settings import ChaosSettings, get_settings

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Failure-injection CLI for the NetHiz company stack. "
    "Usage: python -m chaos.cli <scenario|reset|status> [--customer NH-1000xx] "
    "[--region IST-KAD] [--dry-run] [--json]",
)

CUSTOMER_OPTION = typer.Option(None, "--customer", help="Target customer_no, e.g. NH-100042.")
REGION_OPTION = typer.Option(None, "--region", help="Target region code, e.g. IST-KAD.")
DRY_RUN_OPTION = typer.Option(False, "--dry-run", help="Show what would change without writing anything.")
JSON_OPTION = typer.Option(False, "--json", help="Machine-readable JSON output.")


class ChaosContext:
    def __init__(self, customer: str | None, region: str | None, dry_run: bool, json_output: bool) -> None:
        self.customer = customer
        self.region = region
        self.dry_run = dry_run
        self.json_output = json_output
        self.settings: ChaosSettings = get_settings()


def _echo(ctx: ChaosContext, payload: dict[str, Any], human_lines: list[str]) -> None:
    if ctx.json_output:
        typer.echo(json_module.dumps(payload, indent=2, default=str))
    else:
        for line in human_lines:
            typer.echo(line)


def _fail(ctx: ChaosContext, message: str) -> None:
    if ctx.json_output:
        typer.echo(json_module.dumps({"error": message}, indent=2))
    else:
        typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=1)


def _outcome_lines(outcome: sc.ScenarioOutcome) -> list[str]:
    lines = [f"Scenario: {outcome.scenario}" + (" [DRY RUN]" if outcome.dry_run else "")]
    if outcome.picked:
        lines.append("Picked: " + ", ".join(f"{k}={v}" for k, v in outcome.picked.items()))
    lines.append("Changes:")
    for c in outcome.changes:
        lines.append(f"  - {c}")
    lines.append("")
    lines.append("Expected assistant behaviour:")
    lines.append(f"  {outcome.expected_behavior}")
    return lines


# ---------------------------------------------------------------------------
# a) stuck_provisioning
# ---------------------------------------------------------------------------


@app.command("stuck_provisioning", help="Freeze a provisioning job mid-run (CHAOS_HOLD marker).")
def cmd_stuck_provisioning(
    customer: str = CUSTOMER_OPTION,
    region: str = REGION_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    cctx = ChaosContext(customer, region, dry_run, json_output)
    engine = core_engine(cctx.settings)
    try:
        candidate = sc.pick_stuck_provisioning(engine, cctx.settings, cctx.customer, cctx.region)
    except ChaosError as exc:
        _fail(cctx, str(exc))
        return
    if cctx.dry_run:
        outcome = sc.ScenarioOutcome(
            scenario="stuck_provisioning", dry_run=True, picked=candidate,
            changes=[
                f"[DRY RUN] would freeze the provisioning job for subscription "
                f"{candidate['subscription_id']} with heartbeat_at=now-30m, "
                "last_error_message='CHAOS_HOLD'"
            ],
            expected_behavior="(dry run -- nothing was changed)",
        )
    else:
        outcome = sc.apply_stuck_provisioning(engine, candidate)
    _echo(cctx, asdict(outcome), _outcome_lines(outcome))


# ---------------------------------------------------------------------------
# b) paid_not_active
# ---------------------------------------------------------------------------


@app.command("paid_not_active", help="Payment succeeded but no provisioning job was queued.")
def cmd_paid_not_active(
    customer: str = CUSTOMER_OPTION,
    region: str = REGION_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    cctx = ChaosContext(customer, region, dry_run, json_output)
    engine = core_engine(cctx.settings)
    try:
        candidate = sc.pick_paid_not_active(engine, cctx.customer, cctx.region)
    except ChaosError as exc:
        _fail(cctx, str(exc))
        return
    if cctx.dry_run:
        outcome = sc.ScenarioOutcome(
            scenario="paid_not_active", dry_run=True, picked=candidate,
            changes=[f"[DRY RUN] would delete the queued/running provisioning job for subscription "
                     f"{candidate['subscription_id']}"],
            expected_behavior="(dry run -- nothing was changed)",
        )
    else:
        outcome = sc.apply_paid_not_active(engine, candidate)
    _echo(cctx, asdict(outcome), _outcome_lines(outcome))


# ---------------------------------------------------------------------------
# c) regional_outage
# ---------------------------------------------------------------------------


@app.command("regional_outage", help="Open a critical regional incident, link every active subscriber.")
def cmd_regional_outage(
    customer: str = CUSTOMER_OPTION,
    region: str = REGION_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    cctx = ChaosContext(customer, region, dry_run, json_output)
    engine = core_engine(cctx.settings)
    try:
        region_row = sc.pick_regional_outage(engine, cctx.region)
    except ChaosError as exc:
        _fail(cctx, str(exc))
        return
    if cctx.dry_run:
        outcome = sc.ScenarioOutcome(
            scenario="regional_outage", dry_run=True, picked=dict(region_row),
            changes=[f"[DRY RUN] would open a critical incident for region {region_row['code']} "
                     "and mark every active subscriber's modem offline"],
            expected_behavior="(dry run -- nothing was changed)",
        )
    else:
        outcome = sc.apply_regional_outage(engine, region_row)
    _echo(cctx, asdict(outcome), _outcome_lines(outcome))


# ---------------------------------------------------------------------------
# d) double_charge
# ---------------------------------------------------------------------------


@app.command("double_charge", help="Create a duplicate succeeded charge ~4 minutes after the original.")
def cmd_double_charge(
    customer: str = CUSTOMER_OPTION,
    region: str = REGION_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    cctx = ChaosContext(customer, region, dry_run, json_output)
    engine = core_engine(cctx.settings)
    try:
        candidate = sc.pick_double_charge(engine, cctx.customer, cctx.region)
    except ChaosError as exc:
        _fail(cctx, str(exc))
        return
    if cctx.dry_run:
        outcome = sc.ScenarioOutcome(
            scenario="double_charge", dry_run=True, picked=candidate,
            changes=[f"[DRY RUN] would create a second succeeded charge of "
                     f"{candidate['amount_try']} TRY for {candidate['customer_no']}, ~4 minutes "
                     "after the original"],
            expected_behavior="(dry run -- nothing was changed)",
        )
    else:
        pengine = payment_engine(cctx.settings)
        outcome = sc.apply_double_charge(engine, pengine, candidate)
    _echo(cctx, asdict(outcome), _outcome_lines(outcome))


# ---------------------------------------------------------------------------
# e) missed_installation
# ---------------------------------------------------------------------------


@app.command("missed_installation", help="Mark yesterday's installation appointment as missed.")
def cmd_missed_installation(
    customer: str = CUSTOMER_OPTION,
    region: str = REGION_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    cctx = ChaosContext(customer, region, dry_run, json_output)
    engine = core_engine(cctx.settings)
    try:
        candidate = sc.pick_missed_installation(engine, cctx.customer, cctx.region)
    except ChaosError as exc:
        _fail(cctx, str(exc))
        return
    if cctx.dry_run:
        outcome = sc.ScenarioOutcome(
            scenario="missed_installation", dry_run=True, picked=candidate,
            changes=[f"[DRY RUN] would set appointment {candidate['appointment_id']} to "
                     "scheduled_date=yesterday, status='missed'"],
            expected_behavior="(dry run -- nothing was changed)",
        )
    else:
        outcome = sc.apply_missed_installation(engine, candidate)
    _echo(cctx, asdict(outcome), _outcome_lines(outcome))


# ---------------------------------------------------------------------------
# f) payment_down
# ---------------------------------------------------------------------------


@app.command("payment_down", help="Flip the PSP outage control flag on.")
def cmd_payment_down(
    customer: str = CUSTOMER_OPTION,
    region: str = REGION_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    cctx = ChaosContext(customer, region, dry_run, json_output)
    if cctx.dry_run:
        outcome = sc.ScenarioOutcome(
            scenario="payment_down", dry_run=True, picked={},
            changes=["[DRY RUN] would POST /psp/v1/control {\"outage\": true}"],
            expected_behavior="(dry run -- nothing was changed)",
        )
    else:
        outcome = sc.apply_payment_down(cctx.settings)
    _echo(cctx, asdict(outcome), _outcome_lines(outcome))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status", help="Show which chaos scenarios are currently active.")
def cmd_status(json_output: bool = JSON_OPTION) -> None:
    cctx = ChaosContext(None, None, False, json_output)
    core_eng = core_engine(cctx.settings)

    entries: list[sc.StatusEntry] = []
    with core_eng.connect() as conn:
        entries.append(sc.StatusEntry("stuck_provisioning", False, sc.detect_stuck_provisioning(conn)))
        entries.append(sc.StatusEntry("paid_not_active", False, sc.detect_paid_not_active(conn)))
        entries.append(sc.StatusEntry("regional_outage", False, sc.detect_regional_outage(conn)))
        entries.append(sc.StatusEntry("double_charge", False, sc.detect_double_charge(conn)))
        entries.append(sc.StatusEntry("missed_installation", False, sc.detect_missed_installation(conn)))
    for e in entries:
        e.active = len(e.records) > 0

    payment_down_error = None
    try:
        flags = sc.detect_payment_down(cctx.settings)
        payment_down_active = bool(flags.get("outage"))
    except Exception as exc:  # noqa: BLE001
        flags = {}
        payment_down_active = False
        payment_down_error = str(exc)
    entries.append(sc.StatusEntry(
        "payment_down", payment_down_active,
        [flags] if payment_down_active else [],
    ))

    if cctx.json_output:
        payload: dict[str, Any] = {
            e.scenario: {"active": e.active, "records": e.records} for e in entries
        }
        if payment_down_error:
            payload["payment_down"]["error"] = payment_down_error
        typer.echo(json_module.dumps(payload, indent=2, default=str))
        return

    typer.echo("Chaos status:")
    for e in entries:
        marker = "ACTIVE" if e.active else "clean"
        typer.echo(f"  [{marker}] {e.scenario}")
        if e.scenario == "payment_down" and payment_down_error:
            typer.echo(f"      (could not reach payment-gateway control endpoint: {payment_down_error})")
            continue
        for r in e.records:
            typer.echo(f"      {r}")


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


@app.command("reset", help="Undo every chaos scenario and restore normal control flags.")
def cmd_reset(json_output: bool = JSON_OPTION) -> None:
    cctx = ChaosContext(None, None, False, json_output)
    core_eng = core_engine(cctx.settings)
    payment_eng = payment_engine(cctx.settings)

    results: list[sc.ResetOutcome] = []
    results.append(sc.reset_stuck_provisioning(core_eng))
    results.append(sc.reset_paid_not_active(core_eng))
    results.append(sc.reset_regional_outage(core_eng))
    results.append(sc.reset_double_charge(core_eng, payment_eng))
    results.append(sc.reset_missed_installation(core_eng))

    payment_down_error = None
    try:
        results.append(sc.reset_payment_down(cctx.settings))
    except Exception as exc:  # noqa: BLE001
        payment_down_error = str(exc)

    if cctx.json_output:
        payload: dict[str, Any] = {r.scenario: {"changes": r.changes, "records": r.records} for r in results}
        if payment_down_error:
            payload["payment_down"] = {"error": payment_down_error}
        typer.echo(json_module.dumps(payload, indent=2, default=str))
        return

    typer.echo("Reset complete:")
    any_change = False
    for r in results:
        if r.changes:
            any_change = True
            typer.echo(f"  {r.scenario}:")
            for c in r.changes:
                typer.echo(f"    - {c}")
    if payment_down_error:
        typer.echo(f"  payment_down: could not reach payment-gateway control endpoint: {payment_down_error}")
    elif not any_change:
        typer.echo("  (nothing to reset; system already clean)")


def run() -> None:
    app()


if __name__ == "__main__":
    run()

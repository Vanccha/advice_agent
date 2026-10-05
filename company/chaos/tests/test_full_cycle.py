from __future__ import annotations

import json as json_module

from sqlalchemy import text
from typer.testing import CliRunner

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()

CORE_TABLES = [
    "core.customers",
    "core.subscriptions",
    "core.payments",
    "core.provisioning_jobs",
    "core.modems",
    "core.installation_appointments",
    "core.network_incidents",
    "core.incident_subscriptions",
]
PAYMENT_TABLES = ["psp.charges"]


def table_counts(conn, tables: list[str]) -> dict[str, int]:
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in tables}


def test_status_clean_cli_json(settings):
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    payload = json_module.loads(result.stdout)
    for scenario in (
        "stuck_provisioning", "paid_not_active", "regional_outage",
        "double_charge", "missed_installation", "payment_down",
    ):
        assert payload[scenario]["active"] is False


def test_inject_all_then_reset_all_leaves_unrelated_records_untouched(core_eng, payment_eng, settings):
    with core_eng.connect() as conn:
        before_core = table_counts(conn, CORE_TABLES)
        before_customers = conn.execute(text("SELECT count(*) FROM core.customers")).scalar_one()
    with payment_eng.connect() as conn:
        before_payment = table_counts(conn, PAYMENT_TABLES)

    # Inject every scenario.
    sp = sc.find_stuck_provisioning_candidate(core_eng, None, None) or sc.promote_to_payment_received(
        settings, sc.find_promotable_subscription(core_eng, None, None)
    )
    sc.apply_stuck_provisioning(core_eng, sp)

    pna = sc.find_paid_not_active_candidate(core_eng, None, None) or sc.promote_to_payment_received(
        settings, sc.find_promotable_subscription(core_eng, None, None)
    )
    sc.apply_paid_not_active(core_eng, pna)

    region_row = sc.pick_regional_outage(core_eng, None)
    sc.apply_regional_outage(core_eng, region_row)

    dc = sc.pick_double_charge(core_eng, None, None)
    sc.apply_double_charge(core_eng, payment_eng, dc)

    mi = sc.pick_missed_installation(core_eng, None, None)
    sc.apply_missed_installation(core_eng, mi)

    sc.apply_payment_down(settings)

    # The CLI-level `status` command should see every scenario as active.
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    payload = json_module.loads(result.stdout)
    for scenario in (
        "stuck_provisioning", "paid_not_active", "regional_outage",
        "double_charge", "missed_installation", "payment_down",
    ):
        assert payload[scenario]["active"] is True, f"{scenario} should be active"

    # Now reset through the CLI (as the operator would).
    result = runner.invoke(app, ["reset", "--json"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["status", "--json"])
    payload = json_module.loads(result.stdout)
    for scenario in (
        "stuck_provisioning", "paid_not_active", "regional_outage",
        "double_charge", "missed_installation", "payment_down",
    ):
        assert payload[scenario]["active"] is False, f"{scenario} should be clean after reset"

    # Customer count must never change; chaos never creates/deletes customers.
    with core_eng.connect() as conn:
        after_customers = conn.execute(text("SELECT count(*) FROM core.customers")).scalar_one()
    assert after_customers == before_customers

    # network_incidents/incident_subscriptions and charges/payments must return to baseline
    # counts (chaos is the only writer of incidents; payments/charges only grow by exactly
    # what reset removes again).
    with core_eng.connect() as conn:
        after_core = table_counts(conn, CORE_TABLES)
    with payment_eng.connect() as conn:
        after_payment = table_counts(conn, PAYMENT_TABLES)

    assert after_core["core.network_incidents"] == before_core["core.network_incidents"]
    assert after_core["core.incident_subscriptions"] == before_core["core.incident_subscriptions"]
    # Payments/charges never shrink below baseline: the double_charge duplicate is removed by
    # reset, but paid_not_active may have had to create one genuine new payment first (when no
    # seeded 'payment_received' victim was left to reuse) -- that payment is real and must stay.
    assert after_core["core.payments"] >= before_core["core.payments"]
    assert after_payment["psp.charges"] >= before_payment["psp.charges"]
    assert (
        after_core["core.installation_appointments"] == before_core["core.installation_appointments"]
    )


def test_reset_twice_in_a_row_is_safe(core_eng, payment_eng, settings):
    sc.apply_payment_down(settings)
    region_row = sc.pick_regional_outage(core_eng, None)
    sc.apply_regional_outage(core_eng, region_row)

    sc.reset_stuck_provisioning(core_eng)
    sc.reset_paid_not_active(core_eng)
    sc.reset_regional_outage(core_eng)
    sc.reset_double_charge(core_eng, payment_eng)
    sc.reset_missed_installation(core_eng)
    sc.reset_payment_down(settings)

    # Second pass must be a pure no-op in effect (no errors, nothing left to clean).
    r1 = sc.reset_regional_outage(core_eng)
    r2 = sc.reset_regional_outage(core_eng)
    assert r1.changes == []
    assert r2.changes == []
    assert sc.detect_payment_down(settings)["outage"] is False

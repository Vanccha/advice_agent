from __future__ import annotations

from sqlalchemy import text
from typer.testing import CliRunner

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()


def test_inject_opens_incident_links_subs_and_flips_modems(core_eng):
    region_row = sc.pick_regional_outage(core_eng, None)
    outcome = sc.apply_regional_outage(core_eng, region_row)
    incident_no = outcome.records["incident_no"]
    region_code = region_row["code"]

    with core_eng.connect() as conn:
        incident = conn.execute(
            text(
                "SELECT severity, status, affected_subscription_count FROM core.network_incidents "
                "WHERE incident_no = :no"
            ),
            {"no": incident_no},
        ).mappings().one()
        linked = conn.execute(
            text(
                """
                SELECT s.id, m.status AS modem_status
                FROM core.incident_subscriptions isub
                JOIN core.subscriptions s ON s.id = isub.subscription_id
                JOIN core.modems m ON m.subscription_id = s.id
                WHERE isub.incident_id = (SELECT id FROM core.network_incidents WHERE incident_no = :no)
                """
            ),
            {"no": incident_no},
        ).mappings().all()
        active_subs = conn.execute(
            text(
                """
                SELECT count(*) FROM core.subscriptions s
                JOIN core.customers c ON c.id = s.customer_id
                WHERE c.region_code = :region AND s.status = 'active'
                """
            ),
            {"region": region_code},
        ).scalar_one()

    assert incident["severity"] == "critical"
    assert incident["status"] == "open"
    assert incident["affected_subscription_count"] == active_subs
    assert len(linked) == active_subs
    assert all(row["modem_status"] == "offline" for row in linked)


def test_status_detects_open_incident(core_eng):
    with core_eng.connect() as conn:
        assert sc.detect_regional_outage(conn) == []

    region_row = sc.pick_regional_outage(core_eng, None)
    outcome = sc.apply_regional_outage(core_eng, region_row)

    with core_eng.connect() as conn:
        detected = sc.detect_regional_outage(conn)
    assert any(d["incident_no"] == outcome.records["incident_no"] for d in detected)


def test_reset_deletes_incident_and_restores_modems_and_is_idempotent(core_eng):
    region_row = sc.pick_regional_outage(core_eng, None)
    outcome = sc.apply_regional_outage(core_eng, region_row)
    incident_no = outcome.records["incident_no"]

    reset1 = sc.reset_regional_outage(core_eng)
    assert incident_no in reset1.records["incident_nos"]

    with core_eng.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM core.network_incidents WHERE incident_no = :no"), {"no": incident_no}
        ).scalar_one()
        offline_in_region = conn.execute(
            text(
                """
                SELECT count(*) FROM core.modems m
                JOIN core.subscriptions s ON s.id = m.subscription_id
                JOIN core.customers c ON c.id = s.customer_id
                WHERE c.region_code = :region AND m.status = 'offline'
                """
            ),
            {"region": region_row["code"]},
        ).scalar_one()
    assert remaining == 0
    assert offline_in_region == 0

    with core_eng.connect() as conn:
        assert sc.detect_regional_outage(conn) == []

    reset2 = sc.reset_regional_outage(core_eng)
    assert reset2.changes == []


def test_dry_run_changes_nothing(core_eng):
    with core_eng.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM core.network_incidents")).scalar_one()

    result = runner.invoke(app, ["regional_outage", "--dry-run", "--json"])
    assert result.exit_code == 0

    with core_eng.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM core.network_incidents")).scalar_one()
        detected = sc.detect_regional_outage(conn)
    assert before == after
    assert detected == []

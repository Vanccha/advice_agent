from __future__ import annotations

from typer.testing import CliRunner

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()


def test_inject_flips_outage_flag(settings):
    flags = sc.detect_payment_down(settings)
    assert flags["outage"] is False

    sc.apply_payment_down(settings)

    flags = sc.detect_payment_down(settings)
    assert flags["outage"] is True


def test_reset_restores_defaults_and_is_idempotent(settings):
    sc.apply_payment_down(settings)
    assert sc.detect_payment_down(settings)["outage"] is True

    reset1 = sc.reset_payment_down(settings)
    flags = sc.detect_payment_down(settings)
    assert flags["outage"] is False
    assert flags["force_failure_code"] is None
    assert flags["failure_rate"] == settings.psp_failure_rate
    assert reset1.changes

    reset2 = sc.reset_payment_down(settings)
    assert sc.detect_payment_down(settings)["outage"] is False
    assert reset2.changes  # always re-asserts the defaults; still a no-op in effect


def test_dry_run_changes_nothing(settings):
    before = sc.detect_payment_down(settings)
    assert before["outage"] is False

    result = runner.invoke(app, ["payment_down", "--dry-run", "--json"])
    assert result.exit_code == 0

    after = sc.detect_payment_down(settings)
    assert after["outage"] is False

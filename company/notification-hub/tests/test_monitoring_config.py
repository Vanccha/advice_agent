"""Parse the company/monitoring/*.yml files and check them against docs/contracts.md §2.7.

No database or running services required — pure YAML structure checks.
"""
from __future__ import annotations

from pathlib import Path

import yaml

MONITORING_DIR = Path(__file__).resolve().parents[2] / "monitoring"

EXPECTED_ALERT_DEPARTMENTS = {
    "PaymentGatewayDown": "TECHNICAL_INFRA",
    "CoreApiDown": "TECHNICAL_INFRA",
    "StuckProvisioningJobs": "SUBSCRIPTION_OPS",
    "HighPaymentFailureRate": "BILLING",
    "RegionalOutageDetected": "TECHNICAL_INFRA",
    "MissedInstallations": "FIELD_INSTALL",
}

EXPECTED_SCRAPE_JOBS = {
    "core-api",
    "provisioning-worker",
    "payment-gateway",
    "ticketing",
    "notification-hub",
}


def test_monitoring_directory_has_the_three_files():
    for name in ("prometheus.yml", "rules.yml", "alertmanager.yml"):
        assert (MONITORING_DIR / name).is_file(), f"missing {name}"


def test_rules_yaml_has_six_alerts_with_correct_departments():
    data = yaml.safe_load((MONITORING_DIR / "rules.yml").read_text(encoding="utf-8"))
    rules_by_name = {
        rule["alert"]: rule for group in data["groups"] for rule in group["rules"]
    }
    assert set(rules_by_name) == set(EXPECTED_ALERT_DEPARTMENTS)
    for name, department in EXPECTED_ALERT_DEPARTMENTS.items():
        rule = rules_by_name[name]
        assert rule["labels"]["department"] == department
        assert "severity" in rule["labels"]
        assert rule["annotations"]["summary"]
        assert rule["annotations"]["description"]
        assert "for" in rule
        assert "expr" in rule


def test_prometheus_yaml_has_all_scrape_jobs_at_10s():
    data = yaml.safe_load((MONITORING_DIR / "prometheus.yml").read_text(encoding="utf-8"))
    assert data["global"]["scrape_interval"] == "10s"
    assert data["rule_files"] == ["rules.yml"]
    job_names = {job["job_name"] for job in data["scrape_configs"]}
    assert EXPECTED_SCRAPE_JOBS <= job_names
    for job in data["scrape_configs"]:
        if job["job_name"] in EXPECTED_SCRAPE_JOBS:
            targets = job["static_configs"][0]["targets"]
            assert targets == [f"{job['job_name']}:8000"]


def test_alertmanager_routes_both_receivers_with_continue_true():
    data = yaml.safe_load((MONITORING_DIR / "alertmanager.yml").read_text(encoding="utf-8"))
    routes = data["route"]["routes"]
    by_receiver = {r["receiver"]: r for r in routes}

    assert "nethiz-channels" in by_receiver
    assert "integration-webhook" in by_receiver
    assert by_receiver["nethiz-channels"]["continue"] is True
    assert by_receiver["integration-webhook"]["continue"] is True

    receivers_by_name = {r["name"]: r for r in data["receivers"]}
    nethiz_url = receivers_by_name["nethiz-channels"]["webhook_configs"][0]["url"]
    integration_url = receivers_by_name["integration-webhook"]["webhook_configs"][0]["url"]
    assert nethiz_url == "http://notification-hub:8000/api/v1/alertmanager"
    assert integration_url.startswith("http://")

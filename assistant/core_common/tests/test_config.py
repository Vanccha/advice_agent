from pathlib import Path

import pytest

from core_common.config import ConfigError, clear_tenant_config_cache, load_tenant_config

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config" / "tenants"

REQUIRED_ENV = {
    "MCP_CORE_URL": "http://mcp-core:8000/mcp",
    "MCP_PAYMENT_URL": "http://mcp-payment:8000/mcp",
    "MCP_TICKETING_URL": "http://mcp-ticketing:8000/mcp",
    "MCP_MONITORING_URL": "http://mcp-monitoring:8000/mcp",
    "MCP_NOTIFICATION_URL": "http://mcp-notification:8000/mcp",
}


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_tenant_config_cache()
    yield
    clear_tenant_config_cache()


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_loads_nethiz(monkeypatch: pytest.MonkeyPatch):
    _set_required_env(monkeypatch)
    cfg = load_tenant_config("nethiz", config_dir=CONFIG_DIR)

    assert cfg.tenant.tenant == "nethiz"
    assert cfg.tenant.display_name == "NetHız Telekom"
    assert cfg.adapters["core"].url == "http://mcp-core:8000/mcp"
    assert cfg.departments["BILLING"].display_name_tr == "Faturalama"
    assert cfg.persona.language == "tr"
    assert cfg.policy.actions["issue_refund"].allowed is False
    assert cfg.policy.actions["apply_outage_credit"].max_amount_try == 50
    assert cfg.routing.issue_routing["double_charge"] == "BILLING"


def test_loads_example_tenant_without_code_changes(monkeypatch: pytest.MonkeyPatch):
    _set_required_env(monkeypatch)
    cfg = load_tenant_config("_example", config_dir=CONFIG_DIR)

    assert cfg.tenant.tenant == "_example"
    assert "retry_provisioning_job" in cfg.policy.actions


def test_config_is_cached(monkeypatch: pytest.MonkeyPatch):
    _set_required_env(monkeypatch)
    first = load_tenant_config("nethiz", config_dir=CONFIG_DIR)
    second = load_tenant_config("nethiz", config_dir=CONFIG_DIR)
    assert first is second


def test_missing_env_var_raises_named_error(monkeypatch: pytest.MonkeyPatch):
    _set_required_env(monkeypatch)
    monkeypatch.delenv("MCP_CORE_URL", raising=False)

    with pytest.raises(ConfigError) as exc_info:
        load_tenant_config("nethiz", config_dir=CONFIG_DIR)

    assert "MCP_CORE_URL" in str(exc_info.value)


def test_unknown_tenant_raises():
    with pytest.raises(ConfigError):
        load_tenant_config("does-not-exist", config_dir=CONFIG_DIR)

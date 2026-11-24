"""`routing.yaml: investigation_tools` and the investigation limits (contracts §4.3/§4.6)."""
from __future__ import annotations

import shutil
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
def _env(monkeypatch):
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    yield
    clear_tenant_config_cache()


def _tenant_with_extra_tools(tmp_path: Path, extra_tools: list[str]) -> Path:
    shutil.copytree(CONFIG_DIR / "netswift", tmp_path / "netswift")
    routing = tmp_path / "netswift" / "routing.yaml"
    routing.write_text(
        routing.read_text(encoding="utf-8") + "".join(f"  - {name}\n" for name in extra_tools),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    "write_tool",
    ["retry_provisioning_job", "post_department_message", "create_structured_ticket", "add_ticket_comment"],
)
def test_investigation_tools_must_be_read_only(tmp_path, write_tool):
    config_dir = _tenant_with_extra_tools(tmp_path, [write_tool])
    with pytest.raises(ConfigError, match="read-only"):
        load_tenant_config("netswift", config_dir=config_dir)


def test_duplicate_investigation_tools_are_rejected(tmp_path):
    config_dir = _tenant_with_extra_tools(tmp_path, ["get_service_health"])
    with pytest.raises(ConfigError, match="duplicates"):
        load_tenant_config("netswift", config_dir=config_dir)


@pytest.mark.parametrize("tenant", ["netswift", "_example"])
def test_investigation_config_loads_for_every_tenant(tenant):
    cfg = load_tenant_config(tenant, config_dir=CONFIG_DIR)
    assert "get_service_health" in cfg.routing.investigation_tools
    assert cfg.policy.limits.max_investigation_steps >= 1
    assert cfg.policy.limits.max_clarification_rounds >= 0
    assert not set(cfg.routing.investigation_tools) & set(cfg.policy.actions)

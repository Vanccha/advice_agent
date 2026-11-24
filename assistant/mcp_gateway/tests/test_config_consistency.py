"""Tenant policy and the action runner must agree: no tenant may allow what nothing can run."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core_common.config_models import PolicyFile
from mcp_gateway.action_runner import ACTION_TOOL_MAP

TENANTS_DIR = Path(__file__).resolve().parents[3] / "config" / "tenants"
TENANTS = sorted(p.name for p in TENANTS_DIR.iterdir() if (p / "policy.yaml").exists())


def test_tenants_are_discovered() -> None:
    assert {"netswift", "_example"} <= set(TENANTS)


@pytest.mark.parametrize("tenant", TENANTS)
def test_every_allowed_action_is_mapped_to_a_tool(tenant: str) -> None:
    """Otherwise an approved action would raise `UnmappedActionError` after the customer said yes."""
    raw = yaml.safe_load((TENANTS_DIR / tenant / "policy.yaml").read_text(encoding="utf-8"))
    policy = PolicyFile.model_validate(raw)
    unmapped = [name for name, cfg in policy.actions.items() if cfg.allowed and name not in ACTION_TOOL_MAP]
    assert not unmapped, f"{tenant} allows actions with no entry in ACTION_TOOL_MAP: {unmapped}"

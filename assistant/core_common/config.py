"""Tenant configuration loader: YAML files on disk -> validated ``TenantConfig``.

Where this sits: ``core_common`` (pure logic plus file I/O). Called at API start-up and by
anything that needs policy, routing or persona. It validates into the models in
``config_models.py``; those are re-exported here, so
``from core_common.config import TenantConfig`` keeps working.

Key ideas:
- Config as data: a new tenant is a new folder of YAML, no code change.
- ``${ENV_VAR}`` placeholders are expanded before validation; an unset variable is a hard
  ``ConfigError`` rather than a silent empty string.
- Validate once at load, then cache per (tenant, config dir).

Read next: ``core_common/config_models.py``, ``core_common/settings.py``.
See: docs/contracts.md §2.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from core_common.config_models import (  # noqa: F401  (re-exported for existing importers)
    ActionPolicy,
    AdapterConfig,
    Condition,
    CustomerIdentifierConfig,
    DecisionPolicyConfig,
    DepartmentConfig,
    IncidentHandlingConfig,
    LimitsConfig,
    OnConditionFail,
    PersonaConfig,
    PiiConfig,
    PolicyFile,
    RateLimit,
    RecommendationConfig,
    RecommendationWeights,
    RoutingFile,
    TenantConfig,
    TenantFile,
)
from core_common.settings import get_settings

# --- Errors ------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised for any missing/invalid tenant configuration (including unset env vars)."""


# --- Env var expansion -------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(node: Any) -> Any:
    if isinstance(node, str):

        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(
                    f"Environment variable '{name}' referenced in tenant config is not set"
                )
            return os.environ[name]

        return _ENV_PATTERN.sub(_sub, node)
    if isinstance(node, dict):
        return {key: _expand_env(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_expand_env(value) for value in node]
    return node


# --- Loader ------------------------------------------------------------------

_CACHE: dict[tuple[str, str], TenantConfig] = {}


def _load_yaml_model(path: Path, model: type[BaseModel], tenant_name: str) -> Any:
    if not path.is_file():
        raise ConfigError(f"Missing tenant config file: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc
    expanded = _expand_env(raw)
    try:
        return model.model_validate(expanded)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {path.name} for tenant '{tenant_name}': {exc}") from exc


def load_tenant_config(tenant: str | None = None, config_dir: str | os.PathLike[str] | None = None) -> TenantConfig:
    """Load (and cache) the full configuration for one tenant.

    Args:
        tenant: tenant directory name (e.g. ``"netswift"``, ``"_example"``). Defaults to
            ``get_settings().TENANT``.
        config_dir: overrides ``get_settings().TENANT_CONFIG_DIR`` — mainly for tests.
    """
    settings = get_settings()
    tenant_name = tenant or settings.TENANT
    base_dir = Path(config_dir or settings.TENANT_CONFIG_DIR)

    cache_key = (tenant_name, str(base_dir))
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    tenant_dir = base_dir / tenant_name
    if not tenant_dir.is_dir():
        raise ConfigError(f"Tenant config directory not found: {tenant_dir}")

    tenant_file = _load_yaml_model(tenant_dir / "tenant.yaml", TenantFile, tenant_name)
    policy_file = _load_yaml_model(tenant_dir / "policy.yaml", PolicyFile, tenant_name)
    routing_file = _load_yaml_model(tenant_dir / "routing.yaml", RoutingFile, tenant_name)

    try:
        config = TenantConfig(
            tenant_name=tenant_name,
            tenant=tenant_file,
            policy=policy_file,
            routing=routing_file,
        )
    except ValidationError as exc:
        raise ConfigError(f"Invalid tenant configuration for '{tenant_name}': {exc}") from exc
    _CACHE[cache_key] = config
    return config


def clear_tenant_config_cache() -> None:
    """Test helper: forget every cached tenant config."""
    _CACHE.clear()

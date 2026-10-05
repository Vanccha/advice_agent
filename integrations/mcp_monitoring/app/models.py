"""Pydantic input/output models for every `mcp-monitoring` tool
(docs/contracts.md §3, §2.7)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ServiceStatus = Literal["up", "down", "unknown"]

# The services this adapter reports on, keyed by their Prometheus job name
# (company/monitoring/prometheus.yml) — identical to the service name.
MONITORED_SERVICES: list[str] = [
    "core-api",
    "provisioning-worker",
    "payment-gateway",
    "ticketing",
    "notification-hub",
]


# --------------------------------------------------------------------------
# get_service_health
# --------------------------------------------------------------------------


class GetServiceHealthInput(BaseModel):
    pass


class ServiceHealth(BaseModel):
    service: str
    status: ServiceStatus


class GetServiceHealthOutput(BaseModel):
    prometheus_reachable: bool
    services: list[ServiceHealth]
    detail: Optional[str] = Field(
        default=None, description="set when Prometheus itself could not be queried"
    )


# --------------------------------------------------------------------------
# query_metric
# --------------------------------------------------------------------------


class QueryMetricInput(BaseModel):
    query: str = Field(description="a PromQL expression, e.g. 'up{job=\"core-api\"}'")
    time: Optional[str] = Field(default=None, description="RFC3339 or unix timestamp; defaults to now")


class QueryMetricOutput(BaseModel):
    result_type: str = Field(description="Prometheus resultType: vector|matrix|scalar|string")
    result: Any = Field(description="raw Prometheus result for result_type, passed through unchanged")


# --------------------------------------------------------------------------
# list_active_alerts
# --------------------------------------------------------------------------


class ListActiveAlertsInput(BaseModel):
    severity: Optional[str] = None
    department: Optional[str] = None


class ActiveAlert(BaseModel):
    alertname: str
    status: str
    severity: Optional[str] = None
    department: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    fingerprint: Optional[str] = None
    starts_at: Optional[str] = None


class ListActiveAlertsOutput(BaseModel):
    alerts: list[ActiveAlert]


# --------------------------------------------------------------------------
# POST /webhooks/alertmanager (plain HTTP, not an MCP tool) — real
# Alertmanager webhook schema.
# --------------------------------------------------------------------------


class AlertmanagerWebhookAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "firing"
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: Optional[str] = None
    endsAt: Optional[str] = None
    fingerprint: Optional[str] = None


class AlertmanagerWebhookPayload(BaseModel):
    """Real Alertmanager webhook shape; unknown extra fields (version,
    groupKey, groupLabels, commonLabels, commonAnnotations, externalURL, ...)
    are ignored."""

    model_config = ConfigDict(extra="ignore")

    receiver: Optional[str] = None
    status: Optional[str] = None
    alerts: list[AlertmanagerWebhookAlert] = Field(default_factory=list)


class NormalizedAlert(BaseModel):
    """The payload forwarded to `ASSISTANT_ALERT_WEBHOOK_URL`, one per alert
    (docs/contracts.md §3)."""

    alertname: str
    status: str
    severity: str
    department: str
    fingerprint: str
    summary: str
    description: str
    labels: dict[str, Any]
    starts_at: Optional[str]
    source: Literal["nethiz-alertmanager"] = "nethiz-alertmanager"

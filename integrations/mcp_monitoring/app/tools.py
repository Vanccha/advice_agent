"""`mcp-monitoring` MCP tools (docs/contracts.md §3). The plain HTTP
`/webhooks/alertmanager` endpoint lives in `app.webhooks`, not here — it is
not an MCP tool.
"""
from __future__ import annotations

from typing import Any

import httpx

from common.result import ToolResult, fail, ok
from common.tool_spec import register_tool

from .deps import alertmanager_client, prometheus_client
from .models import (
    MONITORED_SERVICES,
    ActiveAlert,
    GetServiceHealthInput,
    GetServiceHealthOutput,
    ListActiveAlertsInput,
    ListActiveAlertsOutput,
    QueryMetricInput,
    QueryMetricOutput,
    ServiceHealth,
)

SOURCE_MONITORING = "monitoring_api"

# Which service an alert speaks for, when the alert itself carries no `service` label.
# Used to merge the company's alerting into `get_service_health`, because Prometheus `up`
# only proves /metrics answered — not that the service is doing its job.
ALERT_SERVICE_MAP = {
    "PaymentGatewayDown": "payment-gateway",
    "CoreApiDown": "core-api",
    "StuckProvisioningJobs": "provisioning-worker",
}


async def handle_get_service_health(_inp: GetServiceHealthInput) -> ToolResult[Any]:
    """Query Prometheus's `up` metric and report a per-service up/down/unknown
    view. A down or unreachable Prometheus is itself a successful diagnosis
    (`ok=True`, `prometheus_reachable=False`) — this tool must never raise.
    """

    def _unreachable(detail: str) -> ToolResult[Any]:
        return ok(
            GetServiceHealthOutput(
                prometheus_reachable=False,
                services=[ServiceHealth(service=svc, status="unknown") for svc in MONITORED_SERVICES],
                detail=detail,
            ),
            SOURCE_MONITORING,
        )

    try:
        response = await prometheus_client().get("/api/v1/query", params={"query": "up"})
    except httpx.HTTPError as exc:
        return _unreachable(f"prometheus unreachable: {exc!s}")

    if response.status_code != 200:
        return _unreachable(f"prometheus returned HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError:
        return _unreachable("prometheus returned a non-JSON response")

    if body.get("status") != "success":
        return _unreachable(f"prometheus query did not succeed: {body.get('error', 'unknown error')}")

    status_by_job: dict[str, str] = {}
    for item in body.get("data", {}).get("result", []):
        job = item.get("metric", {}).get("job")
        if not job:
            continue
        value = item.get("value", [None, None])[1]
        status_by_job[job] = "up" if value == "1" else "down"

    # Prometheus `up` only says the /metrics endpoint answered. A service can keep serving
    # metrics while refusing all business traffic — the payment gateway does exactly that
    # during an outage — so the company's own firing alerts are merged in. Reporting such a
    # service as "up" would be a lie by omission, and the alert is the signal the company's
    # operators themselves act on.
    alert_down: dict[str, str] = {}
    firing: list[str] = []
    alert_detail: str | None = None
    try:
        alerts_response = await alertmanager_client().get("/api/v2/alerts", params={"active": "true"})
        if alerts_response.status_code == 200:
            for alert in alerts_response.json() or []:
                labels = alert.get("labels") or {}
                name = labels.get("alertname")
                state = ((alert.get("status") or {}).get("state")) or "active"
                if not name or state not in ("active", "firing"):
                    continue
                firing.append(name)
                service = labels.get("service") or ALERT_SERVICE_MAP.get(name)
                if service:
                    alert_down[service] = name
        else:
            alert_detail = f"alertmanager returned HTTP {alerts_response.status_code}"
    except httpx.HTTPError as exc:
        # Never fail the diagnosis because the alerting side is unreachable.
        alert_detail = f"alertmanager unreachable: {exc!s}"

    services = []
    for svc in MONITORED_SERVICES:
        status = status_by_job.get(svc, "unknown")
        reason = None
        if svc in alert_down:
            status = "down"
            reason = f"firing alert: {alert_down[svc]}"
        services.append(ServiceHealth(service=svc, status=status, reason=reason))

    return ok(
        GetServiceHealthOutput(
            prometheus_reachable=True,
            services=services,
            firing_alerts=sorted(set(firing)),
            detail=alert_detail,
        ),
        SOURCE_MONITORING,
    )


async def handle_query_metric(inp: QueryMetricInput) -> ToolResult[Any]:
    """Thin, read-only wrapper over Prometheus `/api/v1/query` (instant
    query). Never touches any other Prometheus endpoint (no admin/TSDB
    access): the PromQL expression is only ever sent as the `query` query
    parameter of this one hardcoded path.
    """
    if not inp.query or not inp.query.strip():
        return fail("INVALID_INPUT", "query must be a non-empty PromQL expression", SOURCE_MONITORING)

    params: dict[str, Any] = {"query": inp.query}
    if inp.time:
        params["time"] = inp.time

    try:
        response = await prometheus_client().get("/api/v1/query", params=params)
    except httpx.HTTPError as exc:
        return fail("UPSTREAM_UNAVAILABLE", f"prometheus unreachable: {exc!s}", SOURCE_MONITORING)

    if response.status_code != 200:
        return fail(
            f"HTTP_{response.status_code}",
            f"prometheus returned HTTP {response.status_code}",
            SOURCE_MONITORING,
        )

    try:
        body = response.json()
    except ValueError:
        return fail("INVALID_RESPONSE", "prometheus returned a non-JSON response", SOURCE_MONITORING)

    if body.get("status") != "success":
        return fail(
            "PROMETHEUS_QUERY_ERROR",
            str(body.get("error", "query failed")),
            SOURCE_MONITORING,
            error_type=body.get("errorType"),
        )

    data = body.get("data", {})
    return ok(
        QueryMetricOutput(result_type=data.get("resultType", ""), result=data.get("result")),
        SOURCE_MONITORING,
    )


async def handle_list_active_alerts(inp: ListActiveAlertsInput) -> ToolResult[Any]:
    try:
        response = await alertmanager_client().get("/api/v2/alerts", params={"active": "true"})
    except httpx.HTTPError as exc:
        return fail("UPSTREAM_UNAVAILABLE", f"alertmanager unreachable: {exc!s}", SOURCE_MONITORING)

    if response.status_code != 200:
        return fail(
            f"HTTP_{response.status_code}",
            f"alertmanager returned HTTP {response.status_code}",
            SOURCE_MONITORING,
        )

    try:
        raw_alerts = response.json()
    except ValueError:
        return fail("INVALID_RESPONSE", "alertmanager returned a non-JSON response", SOURCE_MONITORING)

    alerts: list[ActiveAlert] = []
    for item in raw_alerts:
        labels = item.get("labels", {}) or {}
        if inp.severity and labels.get("severity") != inp.severity:
            continue
        if inp.department and labels.get("department") != inp.department:
            continue
        annotations = item.get("annotations", {}) or {}
        status_obj = item.get("status") or {}
        alerts.append(
            ActiveAlert(
                alertname=labels.get("alertname", "unknown"),
                status=status_obj.get("state", "unknown"),
                severity=labels.get("severity"),
                department=labels.get("department"),
                summary=annotations.get("summary"),
                description=annotations.get("description"),
                fingerprint=item.get("fingerprint"),
                starts_at=item.get("startsAt"),
            )
        )

    return ok(ListActiveAlertsOutput(alerts=alerts), SOURCE_MONITORING)


def register_tools(mcp: Any) -> None:
    register_tool(
        mcp,
        name="get_service_health",
        description="Report per-service up/down/unknown status from Prometheus's up metric.",
        input_model=GetServiceHealthInput,
        output_model=GetServiceHealthOutput,
        handler=handle_get_service_health,
    )
    register_tool(
        mcp,
        name="query_metric",
        description="Run a read-only PromQL instant query against Prometheus.",
        input_model=QueryMetricInput,
        output_model=QueryMetricOutput,
        handler=handle_query_metric,
    )
    register_tool(
        mcp,
        name="list_active_alerts",
        description="List currently active Alertmanager alerts, optionally filtered by severity/department.",
        input_model=ListActiveAlertsInput,
        output_model=ListActiveAlertsOutput,
        handler=handle_list_active_alerts,
    )

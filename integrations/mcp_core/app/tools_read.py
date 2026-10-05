"""Read-only `mcp-core` tools. All but `list_packages` read `diag.*` views
through the `readonly_diag` role; `list_packages` has no view (it's public
reference data) so it reads the core REST catalogue instead.
"""
from __future__ import annotations

from typing import Any

from common.result import ToolResult, fail, ok
from common.tool_spec import register_tool

from .deps import core_api_client
from .diag import DiagQueryError, query_diag
from .models import (
    AffectedSubscription,
    CustomerSummary,
    FindCustomerInput,
    FindCustomerOutput,
    GetActiveIncidentsForRegionInput,
    GetActiveIncidentsForRegionOutput,
    GetIncidentDetailInput,
    GetIncidentDetailOutput,
    GetInstallationStatusInput,
    GetInstallationStatusOutput,
    GetModemStatusInput,
    GetModemStatusOutput,
    GetNotificationHistoryInput,
    GetNotificationHistoryOutput,
    GetProvisioningStatusInput,
    GetProvisioningStatusOutput,
    GetRegionHealthInput,
    GetRegionHealthOutput,
    GetSubscriptionStatusInput,
    GetSubscriptionStatusOutput,
    GetSubscriptionTimelineInput,
    GetSubscriptionTimelineOutput,
    IncidentDetail,
    IncidentSummary,
    InstallationStatus,
    ListPackagesInput,
    ListPackagesOutput,
    ModemStatus,
    NotificationLogEntry,
    PackageSummary,
    ProvisioningJobStatus,
    RegionHealth,
    SubscriptionStatus,
    TimelineEvent,
)

SOURCE_DIAG = "diag_db"
SOURCE_CORE = "core_api"


# --------------------------------------------------------------------------
# find_customer
# --------------------------------------------------------------------------

_FIND_CUSTOMER_SQL = """
    SELECT customer_no, full_name, phone, email, district, city, region_code,
           created_at, subscription_count
    FROM diag.customer_overview
    WHERE (CAST(:customer_no AS text) IS NULL OR customer_no = CAST(:customer_no AS text))
      AND (CAST(:phone AS text) IS NULL OR phone = CAST(:phone AS text))
      AND (CAST(:email AS text) IS NULL OR email = CAST(:email AS text))
    ORDER BY customer_no
    LIMIT 20
"""


async def handle_find_customer(inp: FindCustomerInput) -> ToolResult[Any]:
    if not (inp.customer_no or inp.phone or inp.email):
        return fail("INVALID_INPUT", "provide at least one of customer_no, phone, email", SOURCE_DIAG)
    try:
        rows = query_diag(
            _FIND_CUSTOMER_SQL,
            {"customer_no": inp.customer_no, "phone": inp.phone, "email": inp.email},
        )
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    customers = [CustomerSummary(**row) for row in rows]
    return ok(FindCustomerOutput(customers=customers), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_subscription_status
# --------------------------------------------------------------------------

_SUBSCRIPTION_STATUS_SQL = """
    SELECT customer_no, subscription_id, package_code, package_name, status,
           monthly_price_try, contract_start_date, contract_end_date,
           activated_at, updated_at, region_code
    FROM diag.subscription_status
    WHERE (CAST(:customer_no AS text) IS NULL OR customer_no = CAST(:customer_no AS text))
      AND (CAST(:subscription_id AS bigint) IS NULL OR subscription_id = CAST(:subscription_id AS bigint))
    ORDER BY subscription_id
"""


async def handle_get_subscription_status(inp: GetSubscriptionStatusInput) -> ToolResult[Any]:
    if not (inp.customer_no or inp.subscription_id):
        return fail("INVALID_INPUT", "provide customer_no or subscription_id", SOURCE_DIAG)
    try:
        rows = query_diag(
            _SUBSCRIPTION_STATUS_SQL,
            {"customer_no": inp.customer_no, "subscription_id": inp.subscription_id},
        )
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    subs = [SubscriptionStatus(**row) for row in rows]
    return ok(GetSubscriptionStatusOutput(subscriptions=subs), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_subscription_timeline
# --------------------------------------------------------------------------

_TIMELINE_SQL = """
    SELECT customer_no, subscription_id, event_type, from_status, to_status,
           actor, reason, created_at
    FROM diag.subscription_timeline
    WHERE subscription_id = :subscription_id
    ORDER BY created_at
"""


async def handle_get_subscription_timeline(inp: GetSubscriptionTimelineInput) -> ToolResult[Any]:
    try:
        rows = query_diag(_TIMELINE_SQL, {"subscription_id": inp.subscription_id})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    events = [TimelineEvent(**row) for row in rows]
    return ok(GetSubscriptionTimelineOutput(events=events), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_provisioning_status
# --------------------------------------------------------------------------

_PROVISIONING_STATUS_SQL = """
    SELECT customer_no, subscription_id, job_id, status, attempt_count,
           max_attempts, olt_node, vlan_id, last_error_code, last_error_message,
           queued_at, started_at, heartbeat_at, finished_at, is_stuck
    FROM diag.provisioning_status
    WHERE subscription_id = :subscription_id
    ORDER BY job_id
"""


async def handle_get_provisioning_status(inp: GetProvisioningStatusInput) -> ToolResult[Any]:
    try:
        rows = query_diag(_PROVISIONING_STATUS_SQL, {"subscription_id": inp.subscription_id})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    jobs = [ProvisioningJobStatus(**row) for row in rows]
    return ok(GetProvisioningStatusOutput(jobs=jobs), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_installation_status
# --------------------------------------------------------------------------

_INSTALLATION_STATUS_SQL = """
    SELECT customer_no, subscription_id, appointment_id, scheduled_date,
           time_slot, team_code, status, technician_note, updated_at
    FROM diag.installation_status
    WHERE subscription_id = :subscription_id
    ORDER BY appointment_id
"""


async def handle_get_installation_status(inp: GetInstallationStatusInput) -> ToolResult[Any]:
    try:
        rows = query_diag(_INSTALLATION_STATUS_SQL, {"subscription_id": inp.subscription_id})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    appointments = [InstallationStatus(**row) for row in rows]
    return ok(GetInstallationStatusOutput(appointments=appointments), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_modem_status
# --------------------------------------------------------------------------

_MODEM_STATUS_SQL = """
    SELECT customer_no, subscription_id, serial_no, model, firmware, status,
           provisioned_at
    FROM diag.modem_status
    WHERE subscription_id = :subscription_id
    ORDER BY serial_no
"""


async def handle_get_modem_status(inp: GetModemStatusInput) -> ToolResult[Any]:
    try:
        rows = query_diag(_MODEM_STATUS_SQL, {"subscription_id": inp.subscription_id})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    modems = [ModemStatus(**row) for row in rows]
    return ok(GetModemStatusOutput(modems=modems), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_active_incidents_for_region
# --------------------------------------------------------------------------

_ACTIVE_INCIDENTS_FOR_REGION_SQL = """
    SELECT incident_no, region_code, severity, status, title, description,
           started_at, estimated_resolution_at, affected_subscription_count
    FROM diag.active_incidents
    WHERE region_code = :region_code
    ORDER BY started_at DESC
"""


async def handle_get_active_incidents_for_region(inp: GetActiveIncidentsForRegionInput) -> ToolResult[Any]:
    try:
        rows = query_diag(_ACTIVE_INCIDENTS_FOR_REGION_SQL, {"region_code": inp.region_code})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    incidents = [IncidentSummary(**row) for row in rows]
    return ok(GetActiveIncidentsForRegionOutput(incidents=incidents), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_incident_detail
# --------------------------------------------------------------------------

_INCIDENT_SQL = """
    SELECT incident_no, region_code, severity, status, title, description,
           started_at, estimated_resolution_at, affected_subscription_count
    FROM diag.incidents
    WHERE incident_no = :incident_no
"""

_INCIDENT_AFFECTED_SQL = """
    SELECT customer_no, subscription_id, region_code
    FROM diag.incident_affected
    WHERE incident_no = :incident_no
    ORDER BY customer_no
"""


async def handle_get_incident_detail(inp: GetIncidentDetailInput) -> ToolResult[Any]:
    try:
        incident_rows = query_diag(_INCIDENT_SQL, {"incident_no": inp.incident_no})
        affected_rows = query_diag(_INCIDENT_AFFECTED_SQL, {"incident_no": inp.incident_no})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    if not incident_rows:
        # `diag.incidents` carries every status, so a resolved incident is still
        # answerable — which matters when a customer asks about an outage after the fact.
        return fail("NOT_FOUND", f"no incident {inp.incident_no!r} in diag.incidents", SOURCE_DIAG)
    incident = incident_rows[0]
    affected = [AffectedSubscription(**row) for row in affected_rows]
    detail = IncidentDetail(**incident, affected_subscriptions=affected)
    return ok(GetIncidentDetailOutput(incident=detail), SOURCE_DIAG)


# --------------------------------------------------------------------------
# list_packages (core REST — public catalogue, no diag view)
# --------------------------------------------------------------------------


async def handle_list_packages(inp: ListPackagesInput) -> ToolResult[Any]:
    params: dict[str, Any] = {}
    if inp.profile is not None:
        params["profile"] = inp.profile
    if inp.max_price is not None:
        params["max_price"] = inp.max_price
    if inp.is_active is not None:
        params["is_active"] = inp.is_active
    result = await core_api_client().get("/v1/packages", SOURCE_CORE, params=params)
    if not result.ok:
        return result
    body = result.data or {}
    packages = [PackageSummary(**item) for item in body.get("items", [])]
    return ok(ListPackagesOutput(packages=packages, total=body.get("total", len(packages))), SOURCE_CORE)


# --------------------------------------------------------------------------
# get_notification_history
# --------------------------------------------------------------------------

_NOTIFICATION_HISTORY_SQL = """
    SELECT customer_no, subscription_id, channel, template_code, status, sent_at
    FROM diag.notification_history
    WHERE customer_no = :customer_no
      AND (CAST(:subscription_id AS bigint) IS NULL OR subscription_id = CAST(:subscription_id AS bigint))
    ORDER BY sent_at DESC NULLS LAST
"""


async def handle_get_notification_history(inp: GetNotificationHistoryInput) -> ToolResult[Any]:
    try:
        rows = query_diag(
            _NOTIFICATION_HISTORY_SQL,
            {"customer_no": inp.customer_no, "subscription_id": inp.subscription_id},
        )
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    notifications = [NotificationLogEntry(**row) for row in rows]
    return ok(GetNotificationHistoryOutput(notifications=notifications), SOURCE_DIAG)


# --------------------------------------------------------------------------
# get_region_health
# --------------------------------------------------------------------------

_REGION_HEALTH_SQL = """
    SELECT region_code, total_subscriptions, active_subscriptions,
           stuck_provisioning_jobs, failed_payments_24h, open_incidents
    FROM diag.region_health
    WHERE (CAST(:region_code AS text) IS NULL OR region_code = CAST(:region_code AS text))
    ORDER BY region_code
"""


async def handle_get_region_health(inp: GetRegionHealthInput) -> ToolResult[Any]:
    try:
        rows = query_diag(_REGION_HEALTH_SQL, {"region_code": inp.region_code})
    except DiagQueryError as exc:
        return fail("DIAG_DB_ERROR", exc.message, SOURCE_DIAG)
    regions = [RegionHealth(**row) for row in rows]
    return ok(GetRegionHealthOutput(regions=regions), SOURCE_DIAG)


def register_read_tools(mcp: Any) -> None:
    register_tool(
        mcp,
        name="find_customer",
        description="Find a customer by customer_no, phone, or email (never returns national_id).",
        input_model=FindCustomerInput,
        output_model=FindCustomerOutput,
        handler=handle_find_customer,
    )
    register_tool(
        mcp,
        name="get_subscription_status",
        description="Get subscription status and plan details by customer_no or subscription_id.",
        input_model=GetSubscriptionStatusInput,
        output_model=GetSubscriptionStatusOutput,
        handler=handle_get_subscription_status,
    )
    register_tool(
        mcp,
        name="get_subscription_timeline",
        description="Get the lifecycle event history for one subscription.",
        input_model=GetSubscriptionTimelineInput,
        output_model=GetSubscriptionTimelineOutput,
        handler=handle_get_subscription_timeline,
    )
    register_tool(
        mcp,
        name="get_provisioning_status",
        description="Get provisioning job status (including stuck-job detection) for one subscription.",
        input_model=GetProvisioningStatusInput,
        output_model=GetProvisioningStatusOutput,
        handler=handle_get_provisioning_status,
    )
    register_tool(
        mcp,
        name="get_installation_status",
        description="Get installation appointment status for one subscription.",
        input_model=GetInstallationStatusInput,
        output_model=GetInstallationStatusOutput,
        handler=handle_get_installation_status,
    )
    register_tool(
        mcp,
        name="get_modem_status",
        description="Get modem/CPE status for one subscription.",
        input_model=GetModemStatusInput,
        output_model=GetModemStatusOutput,
        handler=handle_get_modem_status,
    )
    register_tool(
        mcp,
        name="get_active_incidents_for_region",
        description="List active network incidents affecting a region.",
        input_model=GetActiveIncidentsForRegionInput,
        output_model=GetActiveIncidentsForRegionOutput,
        handler=handle_get_active_incidents_for_region,
    )
    register_tool(
        mcp,
        name="get_incident_detail",
        description="Get one incident's detail plus the list of affected customers.",
        input_model=GetIncidentDetailInput,
        output_model=GetIncidentDetailOutput,
        handler=handle_get_incident_detail,
    )
    register_tool(
        mcp,
        name="list_packages",
        description="List the public fiber package catalogue, optionally filtered.",
        input_model=ListPackagesInput,
        output_model=ListPackagesOutput,
        handler=handle_list_packages,
    )
    register_tool(
        mcp,
        name="get_notification_history",
        description="Get SMS/email notification history for a customer.",
        input_model=GetNotificationHistoryInput,
        output_model=GetNotificationHistoryOutput,
        handler=handle_get_notification_history,
    )
    register_tool(
        mcp,
        name="get_region_health",
        description="Get aggregate operational health for one region, or every region if omitted.",
        input_model=GetRegionHealthInput,
        output_model=GetRegionHealthOutput,
        handler=handle_get_region_health,
    )

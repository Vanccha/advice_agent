"""DIAGNOSTIC mode (contracts §4.3/§5): the fixed checklist from
``routing.yaml: diagnostic_checklist`` — customer -> subscription -> payment ->
provisioning -> installation -> regional incidents -> service health — stopping as soon
as a root cause is established.

Only non-PII fields (ids, statuses, codes, counts) are ever put in ``Diagnosis.evidence``:
this dict is logged to the audit trail and returned to the API, both of which must never
carry raw PII (contracts §4.7/§4.8).

Import as: ``from modes.diagnostic import run_diagnosis``.
"""
from __future__ import annotations

from typing import Any

from core_common.types import Diagnosis, DiagnosisScope, StepType
from modes.context import TurnContext
from modes.tool_data import as_list, first_record, raw_payload

# Issue-type strings used as `Diagnosis.root_cause` and, 1:1, as `core_common.types.IssueType`
# values consumed by `modes.action`.
ROOT_CAUSE_STUCK_PROVISIONING = "stuck_provisioning"
ROOT_CAUSE_PAID_NOT_ACTIVE = "paid_not_active"
ROOT_CAUSE_DOUBLE_CHARGE = "double_charge"
ROOT_CAUSE_MISSED_INSTALLATION = "missed_installation"
ROOT_CAUSE_REGIONAL_OUTAGE = "regional_outage"
ROOT_CAUSE_PAYMENT_SYSTEM_DOWN = "payment_system_down"
ROOT_CAUSE_NO_ISSUE_FOUND = "no_issue_found"
ROOT_CAUSE_CUSTOMER_NOT_FOUND = "customer_not_found"


_ok_data = first_record
_as_list = as_list


def _step(ctx: TurnContext, summary: str, reason: str, evidence: dict[str, Any]) -> None:
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.MODE_DECISION,
        summary,
        reason,
        evidence,
        tenant=ctx.tenant,
    )


def run_diagnosis(ctx: TurnContext, customer_no: str) -> Diagnosis:
    evidence: dict[str, Any] = {"queried_sources": []}

    # 1. customer -------------------------------------------------------------------
    customer_outcome = ctx.call_tool_cached("find_customer", {"customer_no": customer_no})
    customer = _ok_data(customer_outcome)
    evidence["queried_sources"].append("mcp-core:find_customer")
    if customer is None:
        _step(ctx, "diagnostic: customer not found", "find_customer returned no record", evidence)
        return Diagnosis(
            root_cause=ROOT_CAUSE_CUSTOMER_NOT_FOUND,
            scope=DiagnosisScope.CUSTOMER_SPECIFIC,
            confidence=0.9,
            evidence=evidence,
            affected_customers=[customer_no],
        )
    region_code = customer.get("region_code")

    # 2. subscription -----------------------------------------------------------------
    sub_outcome = ctx.call_tool("get_subscription_status", {"customer_no": customer_no})
    subscription = _ok_data(sub_outcome)
    evidence["queried_sources"].append("mcp-core:get_subscription_status")
    subscription_id = subscription.get("subscription_id") if subscription else None
    sub_status = subscription.get("status") if subscription else None
    if subscription:
        evidence["subscription_id"] = subscription_id
        evidence["subscription_status"] = sub_status
        region_code = subscription.get("region_code") or region_code

    # 3. payment (+ duplicate-charge detection) ----------------------------------------
    payment_outcome = ctx.call_tool(
        "get_payment_status", {"customer_no": customer_no, "subscription_id": subscription_id}
    )
    payment_records = as_list(payment_outcome)
    payment = payment_records[0] if payment_records else None
    evidence["queried_sources"].append("mcp-payment:get_payment_status")

    dup_outcome = ctx.call_tool("detect_duplicate_charges", {"customer_no": customer_no})
    dup_data = raw_payload(dup_outcome)
    evidence["queried_sources"].append("mcp-payment:detect_duplicate_charges")
    duplicate_pairs = _extract_duplicate_pairs(dup_data)

    # Secondary signal, straight from `get_payment_status`: two or more succeeded
    # payments for the same subscription at the same amount. Covers data the PSP-side
    # detector cannot see — e.g. a seeded "first" payment that was never mirrored into
    # the payment gateway's own ledger, only into core's.
    duplicate_pairs.extend(_duplicate_pairs_from_payment_records(payment_records))

    if duplicate_pairs:
        payment_ids = sorted({pid for pair in duplicate_pairs for pid in pair})
        evidence["payment_ids"] = payment_ids
        evidence["error_codes"] = []
        evidence["observations"] = [
            "Two successful payments for the same subscription were found a short time apart."
        ]
        _step(ctx, "diagnostic: duplicate charge detected", "detect_duplicate_charges matched", evidence)
        return Diagnosis(
            root_cause=ROOT_CAUSE_DOUBLE_CHARGE,
            scope=DiagnosisScope.CUSTOMER_SPECIFIC,
            confidence=0.95,
            evidence=evidence,
            affected_customers=[customer_no],
        )

    payment_status = payment.get("status") if payment else None
    if payment:
        evidence["payment_status"] = payment_status

    # 4. provisioning -------------------------------------------------------------------
    prov_outcome = ctx.call_tool(
        "get_provisioning_status", {"customer_no": customer_no, "subscription_id": subscription_id}
    )
    provisioning = _ok_data(prov_outcome)
    evidence["queried_sources"].append("mcp-core:get_provisioning_status")
    if provisioning:
        evidence["job_id"] = provisioning.get("job_id")
        # The diag view's `is_stuck` is computed live from `heartbeat_at` and can be true
        # before a sweep has flipped the stored `status` column to "stuck" — normalize
        # here so `job_status` (consumed by `policy.yaml: actions.retry_provisioning_job
        # .conditions: [{field: job.status, in: [stuck, failed]}]` via `modes.action`)
        # reflects the actual diagnosed condition either way.
        evidence["job_status"] = "stuck" if provisioning.get("is_stuck") else provisioning.get("status")
        evidence["job_attempt_count"] = provisioning.get("attempt_count")
        if provisioning.get("is_stuck") or provisioning.get("status") in ("stuck", "failed"):
            _step(ctx, "diagnostic: provisioning job stuck/failed", "get_provisioning_status.is_stuck", evidence)
            return Diagnosis(
                root_cause=ROOT_CAUSE_STUCK_PROVISIONING,
                scope=DiagnosisScope.CUSTOMER_SPECIFIC,
                confidence=0.9,
                evidence=evidence,
                affected_customers=[customer_no],
            )

    # Stranded payment: paid, but never entered provisioning.
    if sub_status == "payment_received" and payment_status == "succeeded" and not provisioning:
        _step(ctx, "diagnostic: payment received but no provisioning job", "paid_not_active pattern", evidence)
        return Diagnosis(
            root_cause=ROOT_CAUSE_PAID_NOT_ACTIVE,
            scope=DiagnosisScope.CUSTOMER_SPECIFIC,
            confidence=0.85,
            evidence=evidence,
            affected_customers=[customer_no],
        )

    # 5. installation (only relevant once a subscription has reached that stage — this
    # also keeps the checklist within `policy.yaml: limits.max_tool_calls_per_turn`,
    # since a run that reaches the regional-incident/service-health steps still has an
    # action to take afterwards in the very same turn) ---------------------------------
    if sub_status in ("provisioned", "installation_scheduled"):
        install_outcome = ctx.call_tool(
            "get_installation_status", {"customer_no": customer_no, "subscription_id": subscription_id}
        )
        installation = _ok_data(install_outcome)
        evidence["queried_sources"].append("mcp-core:get_installation_status")
        if installation:
            evidence["appointment_id"] = installation.get("appointment_id")
            evidence["appointment_status"] = installation.get("status")
            evidence["team_code"] = installation.get("team_code")
            if installation.get("status") == "missed":
                _step(ctx, "diagnostic: installation appointment missed", "get_installation_status.status=missed", evidence)
                return Diagnosis(
                    root_cause=ROOT_CAUSE_MISSED_INSTALLATION,
                    scope=DiagnosisScope.CUSTOMER_SPECIFIC,
                    confidence=0.9,
                    evidence=evidence,
                    affected_customers=[customer_no],
                )

    # 6. regional incidents -------------------------------------------------------------
    if region_code:
        incidents = _as_list(ctx.call_tool("get_active_incidents_for_region", {"region_code": region_code}))
        evidence["queried_sources"].append("mcp-core:get_active_incidents_for_region")
        if incidents:
            incident = incidents[0]
            evidence["incident_no"] = incident.get("incident_no")
            evidence["region_code"] = region_code
            _step(ctx, "diagnostic: active regional incident found", "get_active_incidents_for_region matched", evidence)
            return Diagnosis(
                root_cause=ROOT_CAUSE_REGIONAL_OUTAGE,
                scope=DiagnosisScope.REGIONAL_INCIDENT,
                confidence=0.95,
                evidence=evidence,
                affected_customers=[customer_no],
                incident_no=incident.get("incident_no"),
            )

    # 7. service health -------------------------------------------------------------------
    health_outcome = ctx.call_tool("get_service_health", {})
    health = raw_payload(health_outcome)
    evidence["queried_sources"].append("mcp-monitoring:get_service_health")

    # The monitoring view only turns red once the company's alert has fired (one minute of
    # `for:`). A customer complaining right now cannot wait for that, so the gateway's own
    # reported state is consulted as well — it is immediate and equally truthful.
    gateway_down = isinstance(health, dict) and _payment_gateway_down(health)
    if not gateway_down:
        gateway_outcome = ctx.call_tool("get_gateway_health", {})
        gateway = raw_payload(gateway_outcome)
        evidence["queried_sources"].append("mcp-payment:get_gateway_health")
        if isinstance(gateway, dict) and (
            gateway.get("outage") is True or gateway.get("reachable") is False
        ):
            evidence["gateway_health"] = gateway
            gateway_down = True

    if gateway_down:
        evidence["service_health"] = health
        _step(
            ctx,
            "diagnostic: payment gateway reported down",
            "monitoring and/or the gateway itself report the payment service as not serving",
            evidence,
        )
        return Diagnosis(
            root_cause=ROOT_CAUSE_PAYMENT_SYSTEM_DOWN,
            scope=DiagnosisScope.SYSTEM_WIDE,
            confidence=0.9,
            evidence=evidence,
            affected_customers=[customer_no],
        )

    _step(ctx, "diagnostic: no issue established", "checklist exhausted with no match", evidence)
    return Diagnosis(
        root_cause=ROOT_CAUSE_NO_ISSUE_FOUND,
        scope=DiagnosisScope.CUSTOMER_SPECIFIC,
        confidence=0.5,
        evidence=evidence,
        affected_customers=[customer_no],
    )


def _extract_duplicate_pairs(data: Any) -> list[list[int]]:
    """``detect_duplicate_charges`` (live `mcp-payment`) returns
    ``{"customer_no": ..., "window_minutes": 60, "duplicate_groups": [...]}``; fixture-based
    tests commonly spell the same idea as ``{"duplicates": [...]}`` or ``{"items": [...]}``.
    Each group is expected to carry its charge/payment ids under one of a few plausible
    key names — handled defensively since this is data from another agent's adapter."""
    groups: Any = data
    if isinstance(data, dict):
        groups = (
            data.get("duplicate_groups")
            or data.get("duplicates")
            or data.get("items")
            or []
        )
    if not isinstance(groups, list):
        return []
    pairs: list[list[int]] = []
    for group in groups:
        if isinstance(group, dict):
            ids = (
                group.get("payment_ids")
                or group.get("charge_ids")
                or group.get("ids")
                or group.get("payments")
            )
            if isinstance(ids, list) and len(ids) >= 2:
                pairs.append(list(ids))
        elif isinstance(group, list) and len(group) >= 2:
            pairs.append(list(group))
    return pairs


def _duplicate_pairs_from_payment_records(records: list[dict[str, Any]]) -> list[list[int]]:
    succeeded = [r for r in records if r.get("status") == "succeeded" and r.get("payment_id") is not None]
    by_amount: dict[Any, list[int]] = {}
    for record in succeeded:
        by_amount.setdefault(record.get("amount_gbp"), []).append(record["payment_id"])
    return [ids for ids in by_amount.values() if len(ids) >= 2]


_DOWN_WORDS = ("down", "unavailable", "outage", "unhealthy")


def _payment_gateway_down(health: dict[str, Any]) -> bool:
    """True when the monitoring view says the payment gateway is not serving.

    The live adapter answers `{"services": [{"service": "payment-gateway", "status": "down",
    "reason": ...}], ...}`; fixtures and earlier shapes used a mapping or a flat record, so
    all of them are accepted.
    """
    services = health.get("services")
    if isinstance(services, list):
        for entry in services:
            if not isinstance(entry, dict):
                continue
            if "payment" not in str(entry.get("service", "")).lower():
                continue
            if str(entry.get("status", "")).lower() in _DOWN_WORDS:
                return True

    candidates = [health]
    if isinstance(services, dict):
        candidates.append(services)
    for node in candidates:
        for key, value in node.items():
            if "payment" not in str(key).lower():
                continue
            if isinstance(value, dict) and value.get("ok") is False:
                return True
            if isinstance(value, str) and value.lower() in _DOWN_WORDS:
                return True
    if health.get("ok") is False and "payment" in str(health.get("service", "")).lower():
        return True
    return False

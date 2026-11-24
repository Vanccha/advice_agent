"""The fixed diagnostic checklist: walk the customer's records and name a known root cause.

Where this sits: `modes.diagnostic`. `run_diagnosis` is called by `modes.dispatch` for a
problem report; the `Diagnosis` it returns feeds `modes.action`, or `modes.investigation`
when it is `RootCause.NO_ISSUE_FOUND`.

Key ideas:
- Deterministic first: cheap, explainable rules run before any model is involved.
- First match wins, ordered from most to least specific, so one fault gets one name.
- Evidence hygiene: only non-PII fields (ids, statuses, codes, counts) go into
  `Diagnosis.evidence`, because it is audited and returned by the API (contracts §4.7/§4.8).

Checklist order: 1 customer, 2 subscription, 3 payment (+ duplicate charge), 4 provisioning,
5 installation, 6 regional incident, 7 service health.

Read next: modes/diagnostic/payment_checks.py.
See: docs/contracts.md §4.3, §5.
"""
from __future__ import annotations

from typing import Any

from core_common.types import Diagnosis, DiagnosisScope, RootCause, StepType
from modes.context import TurnContext
from modes.diagnostic.payment_checks import (
    duplicate_pairs_from_payment_records,
    extract_duplicate_pairs,
    payment_gateway_down,
)
from modes.tool_data import as_list, first_record, raw_payload


def _step(ctx: TurnContext, summary: str, reason: str, evidence: dict[str, Any]) -> None:
    """Audit one checklist decision so a reviewer can see why this root cause was chosen."""
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.MODE_DECISION,
        summary,
        reason,
        evidence,
        tenant=ctx.tenant,
    )


def run_diagnosis(ctx: TurnContext, customer_no: str) -> Diagnosis:
    """Run the checklist for one customer and return the first root cause that matches.

    Read-only: every lookup goes through the gateway's read tools. The call count stays
    inside `policy.yaml: limits.max_tool_calls_per_turn`, which is why steps such as the
    installation check only run once the subscription has reached that stage.
    """
    evidence: dict[str, Any] = {"queried_sources": []}

    # 1. customer -------------------------------------------------------------------
    customer_outcome = ctx.call_tool_cached("find_customer", {"customer_no": customer_no})
    customer = first_record(customer_outcome)
    evidence["queried_sources"].append("mcp-core:find_customer")
    if customer is None:
        _step(ctx, "diagnostic: customer not found", "find_customer returned no record", evidence)
        return Diagnosis(
            root_cause=RootCause.CUSTOMER_NOT_FOUND,
            scope=DiagnosisScope.CUSTOMER_SPECIFIC,
            confidence=0.9,
            evidence=evidence,
            affected_customers=[customer_no],
        )
    region_code = customer.get("region_code")

    # 2. subscription -----------------------------------------------------------------
    sub_outcome = ctx.call_tool("get_subscription_status", {"customer_no": customer_no})
    subscription = first_record(sub_outcome)
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
    duplicate_pairs = extract_duplicate_pairs(dup_data)

    # Secondary signal, straight from `get_payment_status`: two or more succeeded
    # payments for the same subscription at the same amount. Covers data the PSP-side
    # detector cannot see — e.g. a seeded "first" payment that was never mirrored into
    # the payment gateway's own ledger, only into core's.
    duplicate_pairs.extend(duplicate_pairs_from_payment_records(payment_records))

    if duplicate_pairs:
        payment_ids = sorted({pid for pair in duplicate_pairs for pid in pair})
        evidence["payment_ids"] = payment_ids
        evidence["error_codes"] = []
        evidence["observations"] = [
            "Two successful payments for the same subscription were found a short time apart."
        ]
        _step(ctx, "diagnostic: duplicate charge detected", "detect_duplicate_charges matched", evidence)
        return Diagnosis(
            root_cause=RootCause.DOUBLE_CHARGE,
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
    provisioning = first_record(prov_outcome)
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
                root_cause=RootCause.STUCK_PROVISIONING,
                scope=DiagnosisScope.CUSTOMER_SPECIFIC,
                confidence=0.9,
                evidence=evidence,
                affected_customers=[customer_no],
            )

    # Stranded payment: paid, but never entered provisioning.
    if sub_status == "payment_received" and payment_status == "succeeded" and not provisioning:
        _step(ctx, "diagnostic: payment received but no provisioning job", "paid_not_active pattern", evidence)
        return Diagnosis(
            root_cause=RootCause.PAID_NOT_ACTIVE,
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
        installation = first_record(install_outcome)
        evidence["queried_sources"].append("mcp-core:get_installation_status")
        if installation:
            evidence["appointment_id"] = installation.get("appointment_id")
            evidence["appointment_status"] = installation.get("status")
            evidence["team_code"] = installation.get("team_code")
            if installation.get("status") == "missed":
                _step(ctx, "diagnostic: installation appointment missed", "get_installation_status.status=missed", evidence)
                return Diagnosis(
                    root_cause=RootCause.MISSED_INSTALLATION,
                    scope=DiagnosisScope.CUSTOMER_SPECIFIC,
                    confidence=0.9,
                    evidence=evidence,
                    affected_customers=[customer_no],
                )

    # 6. regional incidents -------------------------------------------------------------
    if region_code:
        incidents = as_list(ctx.call_tool("get_active_incidents_for_region", {"region_code": region_code}))
        evidence["queried_sources"].append("mcp-core:get_active_incidents_for_region")
        if incidents:
            incident = incidents[0]
            evidence["incident_no"] = incident.get("incident_no")
            evidence["region_code"] = region_code
            _step(ctx, "diagnostic: active regional incident found", "get_active_incidents_for_region matched", evidence)
            return Diagnosis(
                root_cause=RootCause.REGIONAL_OUTAGE,
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
    gateway_down = isinstance(health, dict) and payment_gateway_down(health)
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
            root_cause=RootCause.PAYMENT_SYSTEM_DOWN,
            scope=DiagnosisScope.SYSTEM_WIDE,
            confidence=0.9,
            evidence=evidence,
            affected_customers=[customer_no],
        )

    _step(ctx, "diagnostic: no issue established", "checklist exhausted with no match", evidence)
    return Diagnosis(
        root_cause=RootCause.NO_ISSUE_FOUND,
        scope=DiagnosisScope.CUSTOMER_SPECIFIC,
        confidence=0.5,
        evidence=evidence,
        affected_customers=[customer_no],
    )

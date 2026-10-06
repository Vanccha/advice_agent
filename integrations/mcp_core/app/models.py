"""Pydantic input/output models for every `mcp-core` tool (docs/contracts.md §3).

Input models keep every field optional where the company's own data model
allows more than one way to look something up (e.g. `find_customer` by
customer_no/phone/email); the "give me at least one" business rule is
enforced in the tool handler (returning `fail("INVALID_INPUT", ...)`), not in
the model, so a bad combination never raises — it comes back as a normal
`ToolResult`.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# find_customer
# --------------------------------------------------------------------------


class FindCustomerInput(BaseModel):
    customer_no: Optional[str] = Field(default=None, description="e.g. NS-100042")
    phone: Optional[str] = Field(default=None, description="e.g. +447700900167")
    email: Optional[str] = None


class CustomerSummary(BaseModel):
    customer_no: str
    full_name: str
    phone: str
    email: str
    district: str
    city: str
    region_code: str
    created_at: str
    subscription_count: int


class FindCustomerOutput(BaseModel):
    customers: list[CustomerSummary]


# --------------------------------------------------------------------------
# get_subscription_status
# --------------------------------------------------------------------------


class GetSubscriptionStatusInput(BaseModel):
    customer_no: Optional[str] = None
    subscription_id: Optional[int] = None


class SubscriptionStatus(BaseModel):
    customer_no: str
    subscription_id: int
    package_code: str
    package_name: str
    status: str
    monthly_price_gbp: float
    contract_start_date: Optional[str] = None
    contract_end_date: Optional[str] = None
    activated_at: Optional[str] = None
    updated_at: Optional[str] = None
    region_code: str


class GetSubscriptionStatusOutput(BaseModel):
    subscriptions: list[SubscriptionStatus]


# --------------------------------------------------------------------------
# get_subscription_timeline
# --------------------------------------------------------------------------


class GetSubscriptionTimelineInput(BaseModel):
    subscription_id: int


class TimelineEvent(BaseModel):
    customer_no: str
    subscription_id: int
    event_type: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    actor: str
    reason: Optional[str] = None
    created_at: str


class GetSubscriptionTimelineOutput(BaseModel):
    events: list[TimelineEvent]


# --------------------------------------------------------------------------
# get_provisioning_status
# --------------------------------------------------------------------------


class GetProvisioningStatusInput(BaseModel):
    subscription_id: int


class ProvisioningJobStatus(BaseModel):
    customer_no: str
    subscription_id: int
    job_id: int
    status: str
    attempt_count: int
    max_attempts: int
    olt_node: Optional[str] = None
    vlan_id: Optional[int] = None
    last_error_code: Optional[str] = None
    last_error_message: Optional[str] = None
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    finished_at: Optional[str] = None
    is_stuck: bool


class GetProvisioningStatusOutput(BaseModel):
    jobs: list[ProvisioningJobStatus]


# --------------------------------------------------------------------------
# get_installation_status
# --------------------------------------------------------------------------


class GetInstallationStatusInput(BaseModel):
    subscription_id: int


class InstallationStatus(BaseModel):
    customer_no: str
    subscription_id: int
    appointment_id: int
    scheduled_date: str
    time_slot: str
    team_code: str
    status: str
    technician_note: Optional[str] = None
    updated_at: Optional[str] = None


class GetInstallationStatusOutput(BaseModel):
    appointments: list[InstallationStatus]


# --------------------------------------------------------------------------
# get_modem_status
# --------------------------------------------------------------------------


class GetModemStatusInput(BaseModel):
    subscription_id: int


class ModemStatus(BaseModel):
    customer_no: str
    subscription_id: int
    serial_no: str
    model: str
    firmware: Optional[str] = None
    status: str
    provisioned_at: Optional[str] = None


class GetModemStatusOutput(BaseModel):
    modems: list[ModemStatus]


# --------------------------------------------------------------------------
# get_active_incidents_for_region
# --------------------------------------------------------------------------


class GetActiveIncidentsForRegionInput(BaseModel):
    region_code: str


class IncidentSummary(BaseModel):
    incident_no: str
    region_code: str
    severity: str
    status: str
    title: str
    description: Optional[str] = None
    started_at: str
    estimated_resolution_at: Optional[str] = None
    affected_subscription_count: int


class GetActiveIncidentsForRegionOutput(BaseModel):
    incidents: list[IncidentSummary]


# --------------------------------------------------------------------------
# get_incident_detail
# --------------------------------------------------------------------------


class GetIncidentDetailInput(BaseModel):
    incident_no: str


class AffectedSubscription(BaseModel):
    customer_no: str
    subscription_id: int
    region_code: str


class IncidentDetail(BaseModel):
    incident_no: str
    region_code: str
    severity: str
    status: str
    title: str
    description: Optional[str] = None
    started_at: str
    estimated_resolution_at: Optional[str] = None
    affected_subscription_count: int
    affected_subscriptions: list[AffectedSubscription]


class GetIncidentDetailOutput(BaseModel):
    incident: Optional[IncidentDetail] = None


# --------------------------------------------------------------------------
# list_packages (core REST GET /v1/packages — no diag view exists)
# --------------------------------------------------------------------------


class ListPackagesInput(BaseModel):
    profile: Optional[str] = Field(
        default=None,
        description="target_profile filter: student|family|home_office|gamer|basic|premium|small_business",
    )
    max_price: Optional[float] = None
    is_active: Optional[bool] = None


class PackageSummary(BaseModel):
    id: int
    code: str
    name: str
    down_mbps: int
    up_mbps: int
    commitment_months: int
    monthly_price_gbp: float
    setup_fee_gbp: float
    target_profile: str
    max_devices: int
    static_ip: bool
    tv_included: bool
    gaming_optimized: bool
    description: Optional[str] = None
    is_active: bool


class ListPackagesOutput(BaseModel):
    packages: list[PackageSummary]
    total: int


# --------------------------------------------------------------------------
# get_notification_history
# --------------------------------------------------------------------------


class GetNotificationHistoryInput(BaseModel):
    customer_no: str
    subscription_id: Optional[int] = None


class NotificationLogEntry(BaseModel):
    customer_no: str
    subscription_id: Optional[int] = None
    channel: str
    template_code: str
    status: str
    sent_at: Optional[str] = None


class GetNotificationHistoryOutput(BaseModel):
    notifications: list[NotificationLogEntry]


# --------------------------------------------------------------------------
# get_region_health
# --------------------------------------------------------------------------


class GetRegionHealthInput(BaseModel):
    region_code: Optional[str] = Field(default=None, description="omit to list every region")


class RegionHealth(BaseModel):
    region_code: str
    total_subscriptions: int
    active_subscriptions: int
    stuck_provisioning_jobs: int
    failed_payments_24h: int
    open_incidents: int


class GetRegionHealthOutput(BaseModel):
    regions: list[RegionHealth]


# --------------------------------------------------------------------------
# retry_provisioning_job (mutating, core REST, scope provisioning:retry)
# --------------------------------------------------------------------------


class RetryProvisioningJobInput(BaseModel):
    job_id: int


class RetryProvisioningJobOutput(BaseModel):
    job_id: int
    subscription_id: int
    status: str
    attempt_count: int
    max_attempts: int


class EnqueueProvisioningJobInput(BaseModel):
    subscription_id: int


class EnqueueProvisioningJobOutput(BaseModel):
    job_id: int
    subscription_id: int
    status: str
    attempt_count: int
    max_attempts: int


# --------------------------------------------------------------------------
# resend_activation_notification (mutating, core REST, scope notifications:resend)
# --------------------------------------------------------------------------


class ResendActivationNotificationInput(BaseModel):
    customer_no: str
    template_code: str = "ACTIVATION_READY"
    channel: str = "sms"


class ResendActivationNotificationOutput(BaseModel):
    notification_id: int
    customer_no: str
    channel: str
    template_code: str
    status: str
    sent_at: Optional[str] = None


# --------------------------------------------------------------------------
# apply_outage_credit (mutating, core REST, scope credits:write)
# --------------------------------------------------------------------------


class ApplyOutageCreditInput(BaseModel):
    subscription_id: int
    amount_gbp: float
    reason: str
    idempotency_key: Optional[str] = Field(
        default=None, description="omit to let the adapter generate one"
    )


class ApplyOutageCreditOutput(BaseModel):
    credit_id: int
    subscription_id: int
    amount_gbp: float
    reason: str
    created_by: str
    idempotency_key: str
    created_at: str


# --------------------------------------------------------------------------
# request_refund (mutating, core REST, scope billing:refund — partner-integration
# deliberately lacks it: this tool exists to prove the boundary holds)
# --------------------------------------------------------------------------


class RequestRefundInput(BaseModel):
    payment_id: int
    amount_gbp: float
    reason: str


class RequestRefundOutput(BaseModel):
    refund_ref: str
    payment_id: int
    amount_gbp: float
    status: str


# --------------------------------------------------------------------------
# reschedule_installation (mutating, core REST, scope appointments:write —
# partner-integration also lacks this one; field crews own rescheduling)
# --------------------------------------------------------------------------


class RescheduleInstallationInput(BaseModel):
    appointment_id: int
    scheduled_date: str = Field(description="YYYY-MM-DD")
    time_slot: str = Field(description="one of 09-12|12-15|15-18")


class RescheduleInstallationOutput(BaseModel):
    appointment_id: int
    scheduled_date: str
    time_slot: str
    status: str

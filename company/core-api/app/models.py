from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.clock import utcnow
from shared.db import Base

SCHEMA = "core"


class Region(Base):
    __tablename__ = "regions"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    city: Mapped[str] = mapped_column(String(64))
    olt_node_count: Mapped[int] = mapped_column(Integer, default=1)


class Package(Base):
    __tablename__ = "packages"
    __table_args__ = (
        CheckConstraint(
            "target_profile in ('student','family','home_office','gamer','basic',"
            "'premium','small_business')",
            name="ck_packages_target_profile",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    down_mbps: Mapped[int] = mapped_column(Integer)
    up_mbps: Mapped[int] = mapped_column(Integer)
    commitment_months: Mapped[int] = mapped_column(Integer)
    monthly_price_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    setup_fee_gbp: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    target_profile: Mapped[str] = mapped_column(String(32))
    max_devices: Mapped[int] = mapped_column(Integer)
    static_ip: Mapped[bool] = mapped_column(Boolean, default=False)
    tv_included: Mapped[bool] = mapped_column(Boolean, default=False)
    gaming_optimized: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    national_id: Mapped[str] = mapped_column(String(9))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(128))
    address_line: Mapped[str] = mapped_column(Text)
    district: Mapped[str] = mapped_column(String(64))
    city: Mapped[str] = mapped_column(String(64))
    region_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.regions.code"))
    gdpr_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


SUBSCRIPTION_STATUSES = (
    "registered",
    "awaiting_payment",
    "payment_received",
    "provisioning",
    "provisioned",
    "installation_scheduled",
    "active",
    "suspended",
    "cancelled",
)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status in ('" + "','".join(SUBSCRIPTION_STATUSES) + "')",
            name="ck_subscriptions_status",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.customers.id"))
    package_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.packages.id"))
    status: Mapped[str] = mapped_column(String(32), default="registered")
    contract_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    contract_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    monthly_price_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    early_termination_fee_gbp: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SubscriptionEvent(Base):
    __tablename__ = "subscription_events"
    __table_args__ = (
        CheckConstraint(
            "actor in ('system','csr','api_client','job','chaos')",
            name="ck_subscription_events_actor",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.subscriptions.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(32), default="system")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','succeeded','failed','refunded','partially_refunded')",
            name="ck_payments_status",
        ),
        CheckConstraint("method in ('card','eft')", name="ck_payments_method"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.subscriptions.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.customers.id"))
    charge_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    method: Mapped[str] = mapped_column(String(16), default="card")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(
            "status in ('requested','completed','failed')", name="ck_refunds_status"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.payments.id"))
    amount_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), default="requested")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    refund_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Credit(Base):
    __tablename__ = "credits"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.subscriptions.id"))
    amount_gbp: Mapped[float] = mapped_column(Numeric(12, 2))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProvisioningJob(Base):
    __tablename__ = "provisioning_jobs"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued','running','succeeded','failed','stuck')",
            name="ck_provisioning_jobs_status",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.subscriptions.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    olt_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Modem(Base):
    __tablename__ = "modems"
    __table_args__ = (
        CheckConstraint(
            "status in ('in_stock','assigned','shipped','online','offline')",
            name="ck_modems_status",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.subscriptions.id"), nullable=True
    )
    serial_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mac_address: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(64))
    firmware: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="in_stock")
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InstallationAppointment(Base):
    __tablename__ = "installation_appointments"
    __table_args__ = (
        CheckConstraint("time_slot in ('09-12','12-15','15-18')", name="ck_appt_time_slot"),
        CheckConstraint(
            "status in ('scheduled','completed','missed','cancelled','rescheduled')",
            name="ck_appt_status",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.subscriptions.id"))
    scheduled_date: Mapped[date] = mapped_column(Date)
    time_slot: Mapped[str] = mapped_column(String(16))
    team_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    technician_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NetworkIncident(Base):
    __tablename__ = "network_incidents"
    __table_args__ = (
        CheckConstraint("severity in ('minor','major','critical')", name="ck_incident_severity"),
        CheckConstraint("status in ('open','monitoring','resolved')", name="ck_incident_status"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    region_code: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.regions.code"))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    estimated_resolution_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    affected_subscription_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IncidentSubscription(Base):
    __tablename__ = "incident_subscriptions"
    __table_args__ = {"schema": SCHEMA}

    incident_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.network_incidents.id"), primary_key=True
    )
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.subscriptions.id"), primary_key=True
    )


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (
        CheckConstraint("channel in ('sms','email')", name="ck_notification_channel"),
        CheckConstraint("status in ('queued','sent','failed')", name="ck_notification_status"),
        CheckConstraint(
            "template_code in ('ACTIVATION_READY','PAYMENT_RECEIVED','INSTALL_REMINDER',"
            "'OUTAGE_NOTICE','CREDIT_APPLIED')",
            name="ck_notification_template",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.customers.id"))
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.subscriptions.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    template_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ServiceAccount(Base):
    __tablename__ = "service_accounts"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

from __future__ import annotations

from shared.clock import isoformat

from app.models import (
    Customer,
    InstallationAppointment,
    NetworkIncident,
    Payment,
    ProvisioningJob,
    Subscription,
)


def customer_out(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "customer_no": customer.customer_no,
        "full_name": customer.full_name,
        "national_id": customer.national_id,
        "phone": customer.phone,
        "email": customer.email,
        "address_line": customer.address_line,
        "district": customer.district,
        "city": customer.city,
        "region_code": customer.region_code,
        "kvkk_consent_at": isoformat(customer.kvkk_consent_at),
        "created_at": isoformat(customer.created_at),
    }


def subscription_out(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "customer_id": sub.customer_id,
        "package_id": sub.package_id,
        "status": sub.status,
        "contract_start_date": sub.contract_start_date.isoformat() if sub.contract_start_date else None,
        "contract_end_date": sub.contract_end_date.isoformat() if sub.contract_end_date else None,
        "monthly_price_try": float(sub.monthly_price_try),
        "early_termination_fee_try": float(sub.early_termination_fee_try or 0),
        "created_at": isoformat(sub.created_at),
        "updated_at": isoformat(sub.updated_at),
        "activated_at": isoformat(sub.activated_at),
        "suspended_at": isoformat(sub.suspended_at),
        "cancellation_reason": sub.cancellation_reason,
    }


def payment_out(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "subscription_id": payment.subscription_id,
        "customer_id": payment.customer_id,
        "charge_ref": payment.charge_ref,
        "amount_try": float(payment.amount_try),
        "status": payment.status,
        "method": payment.method,
        "idempotency_key": payment.idempotency_key,
        "failure_code": payment.failure_code,
        "failure_message": payment.failure_message,
        "created_at": isoformat(payment.created_at),
        "updated_at": isoformat(payment.updated_at),
    }


def job_out(job: ProvisioningJob) -> dict:
    return {
        "id": job.id,
        "subscription_id": job.subscription_id,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "olt_node": job.olt_node,
        "vlan_id": job.vlan_id,
        "last_error_code": job.last_error_code,
        "last_error_message": job.last_error_message,
        "queued_at": isoformat(job.queued_at),
        "started_at": isoformat(job.started_at),
        "finished_at": isoformat(job.finished_at),
        "heartbeat_at": isoformat(job.heartbeat_at),
        "created_at": isoformat(job.created_at),
        "updated_at": isoformat(job.updated_at),
    }


def appointment_out(appt: InstallationAppointment) -> dict:
    return {
        "id": appt.id,
        "subscription_id": appt.subscription_id,
        "scheduled_date": appt.scheduled_date.isoformat() if appt.scheduled_date else None,
        "time_slot": appt.time_slot,
        "team_code": appt.team_code,
        "status": appt.status,
        "technician_note": appt.technician_note,
        "created_at": isoformat(appt.created_at),
        "updated_at": isoformat(appt.updated_at),
    }


def incident_out(inc: NetworkIncident) -> dict:
    return {
        "id": inc.id,
        "incident_no": inc.incident_no,
        "region_code": inc.region_code,
        "severity": inc.severity,
        "status": inc.status,
        "title": inc.title,
        "description": inc.description,
        "started_at": isoformat(inc.started_at),
        "estimated_resolution_at": isoformat(inc.estimated_resolution_at),
        "resolved_at": isoformat(inc.resolved_at),
        "affected_subscription_count": inc.affected_subscription_count,
        "created_at": isoformat(inc.created_at),
        "updated_at": isoformat(inc.updated_at),
    }


def package_out(pkg) -> dict:
    return {
        "id": pkg.id,
        "code": pkg.code,
        "name": pkg.name,
        "down_mbps": pkg.down_mbps,
        "up_mbps": pkg.up_mbps,
        "commitment_months": pkg.commitment_months,
        "monthly_price_try": float(pkg.monthly_price_try),
        "setup_fee_try": float(pkg.setup_fee_try or 0),
        "target_profile": pkg.target_profile,
        "max_devices": pkg.max_devices,
        "static_ip": pkg.static_ip,
        "tv_included": pkg.tv_included,
        "gaming_optimized": pkg.gaming_optimized,
        "description": pkg.description,
        "is_active": pkg.is_active,
    }


def region_out(region) -> dict:
    return {
        "code": region.code,
        "name": region.name,
        "city": region.city,
        "olt_node_count": region.olt_node_count,
    }

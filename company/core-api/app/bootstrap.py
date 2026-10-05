from __future__ import annotations

import logging

from shared.db import Base, execute_sql_statements, make_engine, wait_for_database

from app.models import *  # noqa: F401,F403  (registers all models on Base.metadata)
from app.seed import run_seed, seed_reference_data
from app.settings import CoreApiSettings

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS core",
    "CREATE SCHEMA IF NOT EXISTS diag",
]

# Diagnostic views: read-only surface for the integration layer. Never expose
# national_id, address_line, or any card/payment-method secret.
DIAG_VIEW_STATEMENTS = [
    """
    CREATE OR REPLACE VIEW diag.customer_overview AS
    SELECT c.customer_no, c.full_name, c.phone, c.email, c.district, c.city,
           c.region_code, c.created_at,
           (SELECT count(*) FROM core.subscriptions s WHERE s.customer_id = c.id) AS subscription_count
    FROM core.customers c
    """,
    """
    CREATE OR REPLACE VIEW diag.subscription_status AS
    SELECT c.customer_no, s.id AS subscription_id, p.code AS package_code, p.name AS package_name,
           s.status, s.monthly_price_try, s.contract_start_date, s.contract_end_date,
           s.activated_at, s.updated_at, c.region_code
    FROM core.subscriptions s
    JOIN core.customers c ON c.id = s.customer_id
    JOIN core.packages p ON p.id = s.package_id
    """,
    """
    CREATE OR REPLACE VIEW diag.payment_status AS
    SELECT c.customer_no, pay.subscription_id, pay.id AS payment_id, pay.charge_ref,
           pay.amount_try, pay.status, pay.method, pay.failure_code, pay.failure_message,
           pay.created_at, pay.updated_at
    FROM core.payments pay
    JOIN core.customers c ON c.id = pay.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.provisioning_status AS
    SELECT c.customer_no, j.subscription_id, j.id AS job_id, j.status, j.attempt_count,
           j.max_attempts, j.olt_node, j.vlan_id, j.last_error_code, j.last_error_message,
           j.queued_at, j.started_at, j.heartbeat_at, j.finished_at,
           (j.status = 'running' AND j.heartbeat_at < now() - interval '5 minutes') AS is_stuck
    FROM core.provisioning_jobs j
    JOIN core.subscriptions s ON s.id = j.subscription_id
    JOIN core.customers c ON c.id = s.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.installation_status AS
    SELECT c.customer_no, a.subscription_id, a.id AS appointment_id, a.scheduled_date,
           a.time_slot, a.team_code, a.status, a.technician_note, a.updated_at
    FROM core.installation_appointments a
    JOIN core.subscriptions s ON s.id = a.subscription_id
    JOIN core.customers c ON c.id = s.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.modem_status AS
    SELECT c.customer_no, m.subscription_id, m.serial_no, m.model, m.firmware, m.status,
           m.provisioned_at
    FROM core.modems m
    JOIN core.subscriptions s ON s.id = m.subscription_id
    JOIN core.customers c ON c.id = s.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.active_incidents AS
    SELECT incident_no, region_code, severity, status, title, description, started_at,
           estimated_resolution_at, affected_subscription_count
    FROM core.network_incidents
    WHERE status IN ('open', 'monitoring')
    """,
    """
    CREATE OR REPLACE VIEW diag.incident_affected AS
    SELECT ni.incident_no, c.customer_no, isub.subscription_id, ni.region_code
    FROM core.incident_subscriptions isub
    JOIN core.network_incidents ni ON ni.id = isub.incident_id
    JOIN core.subscriptions s ON s.id = isub.subscription_id
    JOIN core.customers c ON c.id = s.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.notification_history AS
    SELECT c.customer_no, n.subscription_id, n.channel, n.template_code, n.status, n.sent_at
    FROM core.notification_log n
    JOIN core.customers c ON c.id = n.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.subscription_timeline AS
    SELECT c.customer_no, e.subscription_id, e.event_type, e.from_status, e.to_status,
           e.actor, e.reason, e.created_at
    FROM core.subscription_events e
    JOIN core.subscriptions s ON s.id = e.subscription_id
    JOIN core.customers c ON c.id = s.customer_id
    """,
    """
    CREATE OR REPLACE VIEW diag.region_health AS
    SELECT r.code AS region_code,
           count(DISTINCT s.id) AS total_subscriptions,
           count(DISTINCT s.id) FILTER (WHERE s.status = 'active') AS active_subscriptions,
           count(DISTINCT j.id) FILTER (
               WHERE j.status = 'running' AND j.heartbeat_at < now() - interval '5 minutes'
           ) AS stuck_provisioning_jobs,
           count(DISTINCT pay.id) FILTER (
               WHERE pay.status = 'failed' AND pay.created_at > now() - interval '24 hours'
           ) AS failed_payments_24h,
           count(DISTINCT ni.id) FILTER (WHERE ni.status IN ('open', 'monitoring')) AS open_incidents
    FROM core.regions r
    LEFT JOIN core.customers c ON c.region_code = r.code
    LEFT JOIN core.subscriptions s ON s.customer_id = c.id
    LEFT JOIN core.provisioning_jobs j ON j.subscription_id = s.id
    LEFT JOIN core.payments pay ON pay.subscription_id = s.id
    LEFT JOIN core.network_incidents ni ON ni.region_code = r.code
    GROUP BY r.code
    """,
]


def _grant_statements(diag_user: str) -> list[str]:
    return [
        f"REVOKE ALL ON SCHEMA core FROM {diag_user}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA core FROM {diag_user}",
        f"GRANT USAGE ON SCHEMA diag TO {diag_user}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA diag TO {diag_user}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA diag GRANT SELECT ON TABLES TO {diag_user}",
    ]


def bootstrap(settings: CoreApiSettings | None = None) -> None:
    """Idempotent startup: schemas, tables, diag views, grants, seed."""
    settings = settings or CoreApiSettings()
    engine = make_engine(settings.database_url)
    wait_for_database(engine)

    execute_sql_statements(engine, SCHEMA_STATEMENTS)
    Base.metadata.create_all(engine)
    execute_sql_statements(engine, DIAG_VIEW_STATEMENTS)
    execute_sql_statements(engine, _grant_statements(settings.readonly_diag_user))

    from app.db import get_session_factory

    factory = get_session_factory()
    with factory() as session:
        # Regions/packages/service accounts are catalog data the API always needs.
        seed_reference_data(session, settings)
        if settings.seed_on_startup:
            run_seed(session, settings)

    logger.info("core-api bootstrap complete")

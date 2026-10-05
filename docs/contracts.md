# Contracts — single source of truth

> Every service and adapter in this repository is built against this document.
> If an implementation needs to deviate, this document must be updated **first**.
> Code, identifiers and comments are English. Only end-user-facing strings are Turkish.

Version: 1.0 · Owner: orchestrator

---

## 0. Conventions

- Python 3.12, FastAPI, SQLAlchemy 2.0 (sync engine), Alembic-free: schema is created by
  idempotent SQL in `company/db-init/` + `app/bootstrap.py` per service (keeps compose single-command).
- All services expose: `GET /health` → `{"status":"ok","service":"<name>","version":"<x>"}`,
  `GET /metrics` (Prometheus, via `prometheus_client`), `GET /openapi.json` (FastAPI default).
- Timestamps: UTC, ISO-8601 with `Z`. Money: `numeric(12,2)`, currency always `TRY`.
- IDs: integer surrogate PKs + human-readable business keys (`customer_no`, `TKT-...`, `INC-...`).
- Error envelope (all company services):
  ```json
  {"error": {"code": "SUBSCRIPTION_NOT_FOUND", "message": "...", "details": {}}}
  ```
  HTTP codes: 400 validation, 401 missing/bad key, 403 scope denied, 404 not found,
  409 conflict/illegal transition, 422 FastAPI body validation, 429 rate limit, 503 upstream down.
- Pagination: `?limit=` (default 50, max 200) `&offset=`; responses `{"items":[...],"total":N}`.
- Python packages are importable from the image working dir `/app`. Build contexts:
  - company services → context `company/`, so `company/shared/` is importable as `shared`.
  - integrations → context `integrations/`, `integrations/common/` importable as `common`.
  - assistant → context `assistant/`, importable as `app` root package layout below.

### Repository boundaries (enforced by `tests/architecture/test_boundaries.py`)
1. No module under `assistant/` may import `company`, `shared`, `integrations`, or `common`.
2. No module under `company/` may import `assistant` or `integrations`.
3. No file under `company/` may contain the tokens `assistant`, `advisor`, `chatbot`, `llm`
   (case-insensitive) in endpoint paths, table names, column names or module names.
   Exception file: none. Generic words like "integration", "webhook", "api_client" are allowed.
4. `integrations/` may import `common` and talk HTTP/SQL to company, but must not import
   `assistant` or `company`.

---

## 1. Databases

Two PostgreSQL 16 containers. They never share data.

| Container | Port (host) | Databases | Owner role |
|---|---|---|---|
| `company-db` | 55432 | `nethiz_core`, `nethiz_payment`, `nethiz_ticketing`, `nethiz_notify` | `nethiz` |
| `assistant-db` | 55433 | `assistant_state` | `assistant` |

`company/db-init/01-databases.sql` creates the four databases and the diagnostic role:

```sql
CREATE ROLE readonly_diag LOGIN PASSWORD '<from env>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
-- per database, after schema creation (02-grants.sql, run by core-api bootstrap):
REVOKE ALL ON SCHEMA public, core FROM readonly_diag;
GRANT USAGE ON SCHEMA diag TO readonly_diag;
GRANT SELECT ON ALL TABLES IN SCHEMA diag TO readonly_diag;   -- views only
ALTER DEFAULT PRIVILEGES IN SCHEMA diag GRANT SELECT ON TABLES TO readonly_diag;
```

`readonly_diag` has **no** rights on schema `core` (base tables) and **no** INSERT/UPDATE/DELETE
anywhere. `tests/integration/test_readonly_role.py` proves both.

### 1.1 `nethiz_core` — schema `core`

```
regions(code PK text, name text, city text, olt_node_count int)

packages(id PK, code text uniq, name text, down_mbps int, up_mbps int,
         commitment_months int, monthly_price_try numeric(12,2), setup_fee_try numeric(12,2),
         target_profile text, max_devices int, static_ip bool, tv_included bool,
         gaming_optimized bool, description text, is_active bool default true)
   target_profile ∈ {student, family, home_office, gamer, basic, premium, small_business}

customers(id PK, customer_no text uniq, full_name text, national_id char(11),
          phone text, email text, address_line text, district text, city text,
          region_code text FK regions.code, kvkk_consent_at timestamptz,
          created_at timestamptz default now())
   customer_no format: NH-100001.. (sequential from 100001)
   national_id: 11 digits, FAKE — generated so that it always FAILS the real TC checksum
                (see §6.1); must never resemble a valid identity number.
   phone format: +905XXXXXXXXX (operator prefix from {530,532,535,541,544,551,555})

subscriptions(id PK, customer_id FK, package_id FK, status text,
              contract_start_date date, contract_end_date date,
              monthly_price_try numeric(12,2), early_termination_fee_try numeric(12,2),
              created_at, updated_at, activated_at, suspended_at,
              cancellation_reason text)
   status ∈ {registered, awaiting_payment, payment_received, provisioning,
             provisioned, installation_scheduled, active, suspended, cancelled}

subscription_events(id PK, subscription_id FK, event_type text, from_status text,
                    to_status text, actor text, reason text, payload jsonb, created_at)
   actor ∈ {system, csr, api_client, job, chaos}

payments(id PK, subscription_id FK, customer_id FK, charge_ref text,
         amount_try numeric(12,2), status text, method text, idempotency_key text uniq,
         failure_code text, failure_message text, gateway_response jsonb,
         created_at, updated_at)
   status ∈ {pending, succeeded, failed, refunded, partially_refunded}
   method ∈ {card, eft}

refunds(id PK, payment_id FK, amount_try numeric(12,2), status text, reason text,
        refund_ref text, created_by text, created_at)
   status ∈ {requested, completed, failed}

credits(id PK, subscription_id FK, amount_try numeric(12,2), reason text,
        created_by text, idempotency_key text uniq, created_at)
   "goodwill / outage compensation" — one-time. created_by holds the service account name.

provisioning_jobs(id PK, subscription_id FK, status text, attempt_count int default 0,
                  max_attempts int default 3, olt_node text, vlan_id int,
                  last_error_code text, last_error_message text,
                  queued_at, started_at, finished_at, heartbeat_at, created_at, updated_at)
   status ∈ {queued, running, succeeded, failed, stuck}
   A job is STUCK when status='running' AND heartbeat_at < now() - interval '5 minutes'.
   The worker marks such jobs status='stuck' on each sweep.

modems(id PK, subscription_id FK nullable, serial_no text uniq, mac_address text uniq,
       model text, firmware text, status text, shipped_at, provisioned_at, created_at)
   status ∈ {in_stock, assigned, shipped, online, offline}

installation_appointments(id PK, subscription_id FK, scheduled_date date,
                          time_slot text, team_code text, status text,
                          technician_note text, created_at, updated_at)
   time_slot ∈ {09-12, 12-15, 15-18};  status ∈ {scheduled, completed, missed,
                                                 cancelled, rescheduled}
   team_code format: FIELD-<REGION>-<n>

network_incidents(id PK, incident_no text uniq, region_code text FK, severity text,
                  status text, title text, description text, started_at,
                  estimated_resolution_at, resolved_at,
                  affected_subscription_count int default 0, created_at, updated_at)
   incident_no: INC-2026-001..;  severity ∈ {minor, major, critical}
   status ∈ {open, monitoring, resolved}

incident_subscriptions(incident_id FK, subscription_id FK, PK(incident_id, subscription_id))

notification_log(id PK, customer_id FK, subscription_id FK nullable, channel text,
                 template_code text, status text, payload jsonb, sent_at, created_at)
   channel ∈ {sms, email};  status ∈ {queued, sent, failed}
   template_code ∈ {ACTIVATION_READY, PAYMENT_RECEIVED, INSTALL_REMINDER,
                    OUTAGE_NOTICE, CREDIT_APPLIED}

service_accounts(id PK, name text uniq, api_key_hash text, scopes text[],
                 is_active bool, created_at)
```

#### Seed data (core-api `app/seed.py`, idempotent)
- 12 regions (IST-KAD, IST-BES, IST-BAG, ANK-CAN, ANK-KEC, IZM-KAR, IZM-BOR, BUR-NIL,
  ANT-MUR, ADA-SEY, KON-SEL, TRA-ORT) with Turkish city/district names.
- **7 packages** (all prices TRY/month):

| code | name | down/up | commit | price | setup | profile | devices | extras |
|---|---|---|---|---|---|---|---|---|
| `FIBER_50_OGRENCI` | Öğrenci Fiber 50 | 50/10 | 12 | 269.00 | 0 | student | 8 | – |
| `FIBER_100_TEMEL` | Temel Fiber 100 | 100/20 | 24 | 349.00 | 0 | basic | 12 | – |
| `FIBER_200_AILE` | Aile Fiber 200 | 200/40 | 24 | 459.00 | 0 | family | 20 | tv_included |
| `FIBER_400_HOMEOFFICE` | Home Office Fiber 400 | 400/80 | 24 | 629.00 | 199 | home_office | 30 | static_ip |
| `FIBER_500_OYUNCU` | Oyuncu Fiber 500 | 500/100 | 12 | 749.00 | 199 | gamer | 25 | gaming_optimized |
| `FIBER_1000_PREMIUM` | Premium Fiber 1000 | 1000/200 | 24 | 999.00 | 299 | premium | 50 | tv_included, static_ip, gaming_optimized |
| `FIBER_200_ESNEK` | Esnek Fiber 200 (taahhütsüz) | 200/40 | 0 | 589.00 | 299 | basic | 20 | no commitment |

- **200 customers** with Turkish names, spread over the 12 regions, each with exactly one
  subscription; status distribution: ~150 `active`, 15 `provisioning`, 10 `installation_scheduled`,
  10 `payment_received`, 8 `awaiting_payment`, 5 `suspended`, 2 `cancelled`.
  Active ones have a succeeded payment, an `online` modem, a `completed` appointment and
  `ACTIVATION_READY` notification. Deterministic: `random.Random(20261005)`.
- 2 service accounts (see §2.1).

> **Seeding split (implemented):** regions, the 7 packages and the 2 service accounts are
> reference data the API cannot work without, so `bootstrap.py` always seeds them.
> `SEED_ON_STARTUP` only gates the 200 demo customers and their subscriptions, which lets
> tests exercise the catalogue without 200 extra rows.

#### Diagnostic views — schema `diag` (the ONLY surface `readonly_diag` can read)
National IDs, full addresses and card data are **not** exposed by any view.

```
diag.customer_overview(customer_no, full_name, phone, email, district, city, region_code,
                       created_at, subscription_count)
diag.subscription_status(customer_no, subscription_id, package_code, package_name,
                         status, monthly_price_try, contract_start_date, contract_end_date,
                         activated_at, updated_at, region_code)
diag.payment_status(customer_no, subscription_id, payment_id, charge_ref, amount_try,
                    status, method, failure_code, failure_message, created_at, updated_at)
diag.provisioning_status(customer_no, subscription_id, job_id, status, attempt_count,
                         max_attempts, olt_node, vlan_id, last_error_code,
                         last_error_message, queued_at, started_at, heartbeat_at,
                         finished_at, is_stuck boolean)
diag.installation_status(customer_no, subscription_id, appointment_id, scheduled_date,
                         time_slot, team_code, status, technician_note, updated_at)
diag.modem_status(customer_no, subscription_id, serial_no, model, firmware, status,
                  provisioned_at)
diag.active_incidents(incident_no, region_code, severity, status, title, description,
                      started_at, estimated_resolution_at, affected_subscription_count)
diag.incident_affected(incident_no, customer_no, subscription_id, region_code)
diag.notification_history(customer_no, subscription_id, channel, template_code, status, sent_at)
diag.subscription_timeline(customer_no, subscription_id, event_type, from_status, to_status,
                           actor, reason, created_at)
diag.region_health(region_code, total_subscriptions, active_subscriptions,
                   stuck_provisioning_jobs, failed_payments_24h, open_incidents)
```

### 1.2 `nethiz_payment` — schema `psp`
```
charges(id PK, charge_ref text uniq, customer_ref text, amount_try numeric(12,2),
        status text, method text, card_last4 char(4), idempotency_key text uniq,
        failure_code text, failure_message text, created_at, updated_at)
   status ∈ {pending, succeeded, failed, refunded, partially_refunded}
   failure_code ∈ {INSUFFICIENT_FUNDS, CARD_DECLINED, DO_NOT_HONOR, TIMEOUT, GATEWAY_ERROR}
refunds(id PK, refund_ref text uniq, charge_id FK, amount_try, status, reason, created_at)
webhook_deliveries(id PK, charge_id FK, target_url text, event text, attempt int,
                   response_status int, error text, created_at)
control_flags(key text PK, value jsonb, updated_at)   -- failure_rate, outage, latency_ms
```

### 1.3 `nethiz_ticketing` — schema `tkt`
```
tickets(id PK, ticket_key text uniq, department text, status text, priority text,
        issue_type text, subject text, body text,
        requester_customer_no text, requester_name text, requester_contact text,
        source text, external_ref text, incident_ref text,
        evidence jsonb, attempted_steps jsonb, suggested_next_step text,
        affected_customers jsonb, assignee text, sla_due_at,
        created_at, updated_at, resolved_at)
   ticket_key: TKT-2026-00001..
   department ∈ {TECHNICAL_INFRA, BILLING, SUBSCRIPTION_OPS, FIELD_INSTALL}
   status ∈ {NEW, TRIAGE, IN_PROGRESS, WAITING_CUSTOMER, RESOLVED, CLOSED, REJECTED}
   priority ∈ {LOW, NORMAL, HIGH, URGENT}
   source ∈ {web, phone, api, monitoring}
   external_ref: free text used for idempotency/correlation by API clients (unique index
                 WHERE external_ref IS NOT NULL)
   incident_ref: links the ticket to a core INC-... number (used to avoid duplicate
                 per-customer tickets during a regional outage)
comments(id PK, ticket_id FK, author text, author_type text, body text, is_internal bool,
         created_at)
   author_type ∈ {agent, api_client, system, customer}
status_history(id PK, ticket_id FK, from_status text, to_status text, actor text,
               note text, created_at)
webhook_subscriptions(id PK, name text, target_url text, events text[], is_active bool,
                      secret text, created_at)
webhook_deliveries(id PK, subscription_id FK, ticket_id FK, event text, payload jsonb,
                   attempt int, response_status int, error text, created_at)
departments(code PK, display_name text, email text, channel_slug text)
```
Department display names (Turkish, UI only):
`TECHNICAL_INFRA`=Teknik Altyapı → channel `teknik-altyapi`;
`BILLING`=Faturalama → `faturalama`;
`SUBSCRIPTION_OPS`=Abonelik İşlemleri → `abonelik-islemleri`;
`FIELD_INSTALL`=Saha Kurulum Ekibi → `saha-kurulum`.

Allowed status transitions:
`NEW→{TRIAGE,IN_PROGRESS,REJECTED}`, `TRIAGE→{IN_PROGRESS,WAITING_CUSTOMER,REJECTED}`,
`IN_PROGRESS→{WAITING_CUSTOMER,RESOLVED,REJECTED}`, `WAITING_CUSTOMER→{IN_PROGRESS,RESOLVED}`,
`RESOLVED→{CLOSED,IN_PROGRESS}`, `CLOSED→{}`, `REJECTED→{}`. Violations → 409 `ILLEGAL_TRANSITION`.

SLA: URGENT 2h, HIGH 8h, NORMAL 24h, LOW 72h from creation.

### 1.4 `nethiz_notify` — schema `notify`
```
channels(slug PK, display_name text, description text)
messages(id PK, channel_slug FK, title text, text text, severity text, source text,
         fields jsonb, external_ref text, created_at)
   severity ∈ {info, warning, critical};  source: free text ("core-api", "alertmanager", ...)
```

### 1.5 `assistant_state` — schema `asst` (assistant-owned, company never reads it)
```
conversations(id PK, conversation_id text uniq, tenant text, customer_no text,
              masked_customer_ref text, mode text, state jsonb, created_at, updated_at)
messages(id PK, conversation_id text, role text, content text, mode text,
         masked bool, created_at)
audit_entries(id PK, seq bigserial, conversation_id text, tenant text, step_type text,
              actor text, summary text, reason text, evidence jsonb, policy_decision jsonb,
              tool_name text, tool_input jsonb, tool_output_digest text,
              prev_hash char(64), entry_hash char(64), created_at)
   step_type ∈ {mode_decision, decision_service, tool_call, policy_check, action,
                ticket_created, user_notified, alert_received, approval_requested,
                approval_granted, approval_denied, escalation}
action_records(id PK, conversation_id text, action_name text, params jsonb,
               policy_allowed bool, policy_reason text, executed bool,
               result jsonb, created_at)
ticket_links(id PK, conversation_id text, ticket_key text, department text,
             last_known_status text, notified_status text, created_at, updated_at)
pending_approvals(id PK, conversation_id text, action_name text, params jsonb,
                  prompt_tr text, status text, created_at, resolved_at)
   status ∈ {pending, granted, denied, expired}
alert_events(id PK, alert_fingerprint text, alertname text, severity text, status text,
             labels jsonb, annotations jsonb, received_at, handled bool, handling_note text)
```
`audit_entries` is append-only: a BEFORE UPDATE/DELETE trigger raises an exception, and
`entry_hash = sha256(prev_hash || canonical_json(entry_without_hash))`.

---

## 2. Company REST APIs

### 2.1 Authentication (core-api, payment, ticketing, notify)
Header `X-API-Key: <plain key>`; stored as `sha256(key)`. Core-api service accounts seeded:

| name | key env var | scopes |
|---|---|---|
| `nethiz-crm` | `CORE_API_KEY_CRM` | `customers:*`, `subscriptions:*`, `payments:*`, `billing:refund`, `provisioning:*`, `incidents:*`, `appointments:*`, `notifications:send`, `credits:write` |
| `partner-integration` | `CORE_API_KEY_PARTNER` | `customers:read`, `subscriptions:read`, `payments:read`, `provisioning:read`, `provisioning:retry`, `incidents:read`, `appointments:read`, `notifications:resend`, `credits:write`, `tickets:write` |

The integration layer uses **`partner-integration`** only. It deliberately lacks
`billing:refund`, `subscriptions:write` and `appointments:write` → refunds/plan changes fail
with `403 SCOPE_DENIED` even if something bypassed the assistant policy engine.
Missing key → 401 `MISSING_API_KEY`; unknown/inactive → 401 `INVALID_API_KEY`.

### 2.2 core-api (`http://core-api:8000`, host `:8001`)

| Method | Path | Scope | Notes |
|---|---|---|---|
| GET | `/health` `/metrics` | – | |
| GET | `/v1/regions` | `incidents:read` | |
| GET | `/v1/packages` `?profile=&max_price=&is_active=` | – | public catalogue |
| GET | `/v1/packages/{code}` | – | |
| POST | `/v1/customers` | `customers:write` | body: full_name, national_id, phone, email, address_line, district, city, region_code, kvkk_consent → 201 customer |
| GET | `/v1/customers` `?customer_no=&phone=&email=&region_code=` | `customers:read` | |
| GET | `/v1/customers/{customer_no}` | `customers:read` | includes subscriptions summary |
| POST | `/v1/subscriptions` | `subscriptions:write` | body: customer_no, package_code → status `registered` |
| GET | `/v1/subscriptions/{id}` | `subscriptions:read` | full detail incl. latest payment/job/appointment |
| GET | `/v1/subscriptions` `?customer_no=&status=&region_code=` | `subscriptions:read` | |
| POST | `/v1/subscriptions/{id}/payments` | `payments:write` | body: amount_try?, method, card_token?, idempotency_key → calls PSP, creates `payments` row. If the PSP is unreachable the `pending` row is committed before the 503 propagates, so retrying the same key returns that row instead of charging twice. |
| GET | `/v1/subscriptions/{id}/payments` | `payments:read` | |
| POST | `/v1/subscriptions/{id}/transitions` | `subscriptions:write` | body: to_status, reason → 409 on illegal transition |
| POST | `/v1/subscriptions/{id}/cancel` | `subscriptions:write` | |
| POST | `/v1/refunds` | `billing:refund` | body: payment_id, amount_try, reason → PSP refund |
| POST | `/v1/credits` | `credits:write` | body: subscription_id, amount_try, reason, idempotency_key. **Server-side cap:** `CREDIT_MAX_PER_REQUEST_TRY` (default 250) → 400 `CREDIT_LIMIT_EXCEEDED` |
| GET | `/v1/provisioning-jobs` `?subscription_id=&status=` | `provisioning:read` | |
| POST | `/v1/provisioning-jobs/{id}/retry` | `provisioning:retry` | queued again, attempt_count+1; 409 if status ∈ {queued,running,succeeded} |
| GET | `/v1/installation-appointments` `?subscription_id=&status=&team_code=` | `appointments:read` | |
| POST | `/v1/installation-appointments/{id}/reschedule` | `appointments:write` | body: scheduled_date, time_slot |
| GET | `/v1/incidents` `?region_code=&status=&severity=` | `incidents:read` | |
| GET | `/v1/incidents/{incident_no}` | `incidents:read` | includes affected customer_no list |
| POST | `/v1/notifications/resend` | `notifications:resend` | body: customer_no, template_code, channel → new `notification_log` row; 429 if the same (customer, template) was sent < `RESEND_COOLDOWN_SECONDS` (default 120) ago → `RESEND_COOLDOWN` |
| POST | `/v1/webhooks/payment` | shared secret `X-Webhook-Secret` | PSP → core status sync |

Lifecycle enforced in `app/lifecycle.py`:
```
registered → awaiting_payment → payment_received → provisioning → provisioned
          → installation_scheduled → active ;  active ↔ suspended ;  any → cancelled
```
`POST /v1/subscriptions/{id}/payments` moves `registered|awaiting_payment → awaiting_payment`
and, on PSP success, `→ payment_received` + enqueues a `provisioning_jobs` row (`queued`).

### 2.3 provisioning-worker (no HTTP API except `:8000/health` + `/metrics`)
Loop every `WORKER_INTERVAL_SECONDS` (default 5):
1. claim one `queued` job → `running`, set `heartbeat_at`;
2. simulate 2–6 s of work, write `heartbeat_at` each second;
3. success (default 90 %, `PROVISION_SUCCESS_RATE`) → job `succeeded`, assign/activate modem,
   subscription → `provisioned`, create `installation_appointments` row (+3..10 days),
   subscription → `installation_scheduled`;
4. failure → `failed` + `last_error_code` ∈ {OLT_PORT_BUSY, VLAN_CONFLICT, CPE_TIMEOUT}.
   **The worker does not re-queue by itself**: a failed job waits for an explicit
   `POST /v1/provisioning-jobs/{id}/retry`, which is what the `attempt_count < max_attempts`
   limit guards. (This is deliberate — it is the company behaviour the product fixes.);
5. sweep: `running` jobs with `heartbeat_at < now()-STUCK_AFTER_SECONDS` (300) → `stuck`;
6. jobs with `chaos_hold=true` marker in `payload` are intentionally **not** touched
   (chaos scenario a). Marker lives in `provisioning_jobs.last_error_message = 'CHAOS_HOLD'`
   + `status='running'` with a frozen heartbeat — no assistant-specific column.

Metrics: `nethiz_provisioning_jobs_total{status}`, `nethiz_provisioning_jobs_stuck`,
`nethiz_provisioning_job_duration_seconds`, `nethiz_worker_sweeps_total`.

### 2.4 payment-gateway-mock (`http://payment-gateway:8000`, host `:8002`)

| Method | Path | Notes |
|---|---|---|
| POST | `/psp/v1/charges` | body: amount_try, currency, customer_ref, method, card_token?, idempotency_key, callback_url? → 201 `{charge_ref,status,failure_code?}`. Same idempotency_key returns the original charge (200). |
| GET | `/psp/v1/charges/{charge_ref}` | |
| GET | `/psp/v1/charges?customer_ref=&status=` | |
| POST | `/psp/v1/charges/{charge_ref}/refunds` | body: amount_try, reason → 201; 409 `ALREADY_REFUNDED`, 422 `REFUND_EXCEEDS_CHARGE` |
| GET | `/psp/v1/control` / POST | `{failure_rate: 0.1, outage: false, latency_ms: 0, force_failure_code: null}` (used by chaos; auth: `X-API-Key`) |

When `outage=true`: every endpoint except `/health` and `/psp/v1/control` returns
503 `GATEWAY_UNAVAILABLE`, and `/health` returns 503 so Prometheus `up` goes 0.
Metrics: `psp_charges_total{status}`, `psp_refunds_total{status}`, `psp_request_duration_seconds`,
`psp_outage` gauge.

### 2.5 ticketing (`http://ticketing:8000`, host `:8003`)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/tickets` | body in §4.1. 200 (not 201) + existing ticket when `external_ref` already exists (idempotent). |
| GET | `/api/v1/tickets` `?department=&status=&customer_no=&incident_ref=&external_ref=&limit=&offset=` | |
| GET | `/api/v1/tickets/{ticket_key}` | includes comments + status_history |
| PATCH | `/api/v1/tickets/{ticket_key}` | body: status?, assignee?, department?, priority?, note? |
| POST | `/api/v1/tickets/{ticket_key}/comments` | body: author, author_type, body, is_internal |
| GET | `/api/v1/departments` | |
| GET/POST/DELETE | `/api/v1/webhook-subscriptions` | generic integration webhooks |
| GET | `/agent`, `/agent/tickets/{key}` | Jinja UI: filters by department/status, comment box, status dropdown |

Outgoing webhook (`event: ticket.status_changed` | `ticket.commented` | `ticket.created`):
```json
{"event":"ticket.status_changed","ticket_key":"TKT-2026-00014","department":"BILLING",
 "old_status":"NEW","new_status":"IN_PROGRESS","priority":"HIGH","assignee":"ayse.kaya",
 "external_ref":"conv-9f2c:double_charge","requester_customer_no":"NH-100042",
 "comment":"İade talebi muhasebeye iletildi.","occurred_at":"2026-10-05T12:00:00Z"}
```
Signed with `X-Webhook-Signature: sha256=<hmac(secret, body)>`. Delivery attempts: 3, 2 s apart.
Metrics: `tkt_tickets_total{department,status}`, `tkt_open_tickets{department}`,
`tkt_webhook_deliveries_total{result}`.

### 2.6 notification-hub (`http://notification-hub:8000`, host `:8004`)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/channels/{slug}/messages` | body: title, text, severity, source, fields{}, external_ref |
| GET | `/api/v1/channels/{slug}/messages?limit=` | |
| GET | `/api/v1/channels` | |
| POST | `/api/v1/alertmanager` | Alertmanager receiver → fans alerts into channels by label `department` (default `operasyon-genel`) |
| GET | `/` , `/c/{slug}` | Jinja UI, auto-refresh every 5 s |

Channels seeded: `teknik-altyapi`, `faturalama`, `abonelik-islemleri`, `saha-kurulum`,
`operasyon-genel`.

### 2.7 monitoring
`company/monitoring/prometheus.yml` scrapes core-api, provisioning-worker, payment-gateway,
ticketing, notification-hub every 10 s (`:8000/metrics` inside the compose network).

Rules (`company/monitoring/rules.yml`), all with `labels: {severity, department}`:

| alert | expr | for | department |
|---|---|---|---|
| `PaymentGatewayDown` | `up{job="payment-gateway"} == 0 or psp_outage == 1` | 1m | TECHNICAL_INFRA |
| `CoreApiDown` | `up{job="core-api"} == 0` | 1m | TECHNICAL_INFRA |
| `StuckProvisioningJobs` | `nethiz_provisioning_jobs_stuck > 0` | 2m | SUBSCRIPTION_OPS |
| `HighPaymentFailureRate` | `rate(psp_charges_total{status="failed"}[5m]) / clamp_min(rate(psp_charges_total[5m]),0.001) > 0.5` | 3m | BILLING |
| `RegionalOutageDetected` | `nethiz_open_incidents{severity="critical"} > 0` | 1m | TECHNICAL_INFRA |
| `MissedInstallations` | `nethiz_missed_appointments_24h > 0` | 5m | FIELD_INSTALL |

Alertmanager (`alertmanager.yml`) has two receivers for every alert (`continue: true`):
1. `nethiz-channels` → `http://notification-hub:8000/api/v1/alertmanager`
2. `integration-webhook` → `${ALERT_INTEGRATION_WEBHOOK_URL}`
   (compose default: `http://mcp-monitoring:8000/webhooks/alertmanager`)

> Receiver 2 is **configuration**, not code: a company pointing Alertmanager at a third-party
> tool's webhook. No company source file mentions the assistant.

---

## 3. Integration layer (MCP)

Five MCP servers, each its own container, **streamable HTTP** transport at `POST /mcp`
(`mcp` Python SDK, `FastMCP`). Env `COMPANY_*_BASE_URL`, `COMPANY_API_KEY`,
`DIAG_DATABASE_URL` (the `readonly_diag` DSN).

Rules:
- **Reads** → `diag.*` views via `DIAG_DATABASE_URL` (SQLAlchemy, `readonly_diag`), or company
  REST GET where no view exists.
- **Writes/actions** → company REST POST/PATCH only. `integrations/common/readonly_db.py`
  opens the session with `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` and rejects
  any statement not starting with `SELECT`/`WITH`.
- Every tool: snake_case name, 1-line Turkish-free English description, Pydantic input and
  output models, and a `ToolResult` envelope `{ok, data, error, source}` where
  `source ∈ {diag_db, core_api, payment_api, ticketing_api, monitoring_api, notification_api}`.

| Server (port) | Tools |
|---|---|
| `mcp-core` (9101) | `find_customer`, `get_subscription_status`, `get_subscription_timeline`, `get_provisioning_status`, `get_installation_status`, `get_modem_status`, `get_active_incidents_for_region`, `get_incident_detail`, `list_packages`, `get_notification_history`, `get_region_health`, `retry_provisioning_job`*, `resend_activation_notification`*, `apply_outage_credit`*, `request_refund`* (always returns the 403 from core-api — proves the hard boundary), `reschedule_installation`* |
| `mcp-payment` (9102) | `get_payment_status`, `list_customer_charges`, `detect_duplicate_charges`, `get_gateway_health` |
| `mcp-ticketing` (9103) | `create_structured_ticket`, `get_ticket`, `list_customer_tickets`, `find_tickets_by_incident`, `add_ticket_comment` |
| `mcp-monitoring` (9104) | `get_service_health`, `query_metric`, `list_active_alerts`, plus `POST /webhooks/alertmanager` → forwards normalized alert to `ASSISTANT_ALERT_WEBHOOK_URL` |
| `mcp-notification` (9105) | `post_department_message`, `list_channel_messages` |

`*` = mutating; routed through company REST with the `partner-integration` key.

Normalized alert payload forwarded to the assistant:
```json
{"alertname":"PaymentGatewayDown","status":"firing","severity":"critical",
 "department":"TECHNICAL_INFRA","fingerprint":"ab12...","summary":"...","description":"...",
 "labels":{...},"starts_at":"2026-10-05T12:00:00Z","source":"nethiz-alertmanager"}
```

---

## 4. Assistant-side contracts

### 4.1 Structured ticket payload (assistant → `POST /api/v1/tickets`)
```json
{
  "department": "BILLING",
  "issue_type": "double_charge",
  "priority": "HIGH",
  "subject": "Çift tahsilat — NH-100042 (2 x 459,00 TRY)",
  "body": "<Turkish narrative for the human agent>",
  "source": "api",
  "external_ref": "conv-9f2c1a:double_charge",
  "incident_ref": null,
  "requester": {"customer_no": "NH-100042", "name": "A** K***",
                "contact": "+90 5** *** ** 31"},
  "evidence": {
    "record_ids": {"subscription_id": 42, "payment_ids": [88, 89]},
    "error_codes": [],
    "observations": ["İki ödeme aynı gün, aynı tutar, 4 dakika arayla succeeded."],
    "queried_sources": ["diag.payment_status", "payment_api:list_customer_charges"]
  },
  "attempted_steps": [
    {"step": "get_payment_status", "result": "2 succeeded charges found", "outcome": "info"},
    {"step": "policy_check:issue_refund", "result": "denied: refund_not_permitted",
     "outcome": "blocked"}
  ],
  "affected_customers": ["NH-100042"],
  "suggested_next_step": "88 numaralı ödemenin iadesi (459,00 TRY) onaylanmalı.",
  "urgency_reason": "Müşteriden iki kez tahsilat alındı, yasal süre içinde iade gerekiyor."
}
```
`issue_type` ∈ {`stuck_provisioning`, `paid_not_active`, `regional_outage`, `double_charge`,
`missed_installation`, `payment_system_down`, `plan_change_request`, `refund_request`,
`infrastructure_repair`, `other`}.

### 4.2 Assistant HTTP API (`http://assistant:8000`, host `:8080`)

| Method | Path | Notes |
|---|---|---|
| GET | `/` | demo page: fake login by `customer_no` + embedded chat widget |
| POST | `/api/login` | body: `{customer_no}` → session cookie (demo only, no password) |
| POST | `/api/chat` | body: `{conversation_id?, message, customer_no?}` → `{conversation_id, mode, reply_tr, actions[], ticket_key?, requires_approval?, approval_id?}` |
| GET | `/api/chat/stream` | SSE token stream of the same turn |
| POST | `/api/approvals/{approval_id}` | `{decision: "granted"\|"denied"}` |
| GET | `/api/conversations/{conversation_id}/audit` | audit trail for the demo |
| POST | `/webhooks/ticket` | ticketing `ticket.status_changed` (HMAC verified) → notifies user |
| POST | `/webhooks/alert` | normalized alert from `mcp-monitoring` → proactive diagnosis |
| GET | `/health` `/metrics` | |

> Shared assistant-side vocabulary (enums, `Decision[T]`, `AdvisoryProfile`, `PolicyDecision`,
> settings, tenant-config loader, `assistant_state` models) lives in **`assistant/core_common/`**
> and is imported as `core_common.*`. The name avoids the top-level `common` package, which the
> boundary test forbids inside `assistant/` (that name belongs to the integration layer).

### 4.3 Modes (explicit state machine, `assistant/modes/machine.py`)
States: `ROUTER → {ADVISORY, DIAGNOSTIC, STATUS_QUERY} → ACTION → CLOSING`,
plus `AWAITING_APPROVAL` and `ESCALATED`. Transitions are table-driven; no free-form loop.
`MAX_TURNS_PER_MODE`, `MAX_TOOL_CALLS_PER_TURN` (default 8) are config.

- `ROUTER` → `DecisionService.classify_intent` → one of
  `advisory | problem_report | status_query | smalltalk | out_of_scope`.
- `ADVISORY`: asks 3–5 questions (usage purpose, household size, device count, budget,
  commitment preference, gaming/streaming/work-from-home needs) → fills
  `AdvisoryProfile` → **deterministic** `recommend_packages(profile, packages)` → LLM only
  verbalizes the result in Turkish. The LLM may not invent or reorder packages.
- `DIAGNOSTIC`: fixed checklist — customer record → subscription status → payment status →
  provisioning status → installation status → regional incidents → service health.
  Produces a `Diagnosis` with `root_cause`, `scope ∈ {customer_specific, regional_incident,
  system_wide}`, `confidence`, `evidence`.
- `ACTION`: candidate action → **policy engine** → execute | ask approval | escalate (ticket).
- `STATUS_QUERY`: reports on existing ticket(s)/subscription without opening anything new.

### 4.4 `recommend_packages` (deterministic, `assistant/recommendation/engine.py`)
Pure function, no LLM. Input `AdvisoryProfile`:
```
usage: list[student|family|home_office|gaming|streaming|basic]
household_size: int ; device_count: int ; budget_try: float|None
commitment_preference: none|12|24|any ; needs_static_ip: bool ; needs_tv: bool
```
Score per package = weighted sum (weights in `config/tenants/nethiz/routing.yaml`):
speed adequacy (device_count × 25 Mbps target, 35 %), budget fit (25 %, hard filter when
`budget_try` exceeded by > 15 %), profile match (20 %), commitment match (10 %),
extras match (10 %). Returns top-3 with `score`, `reasons[]` (Turkish reason codes resolved
from a table, not generated), `is_best`. Ties broken by lower price, then shorter commitment.

### 4.5 `DecisionService` (`assistant/decision/base.py`)
```python
class DecisionService(Protocol):
    def classify_intent(self, ctx: DecisionContext) -> Decision[Intent]: ...
    def choose_department(self, ctx: DecisionContext) -> Decision[Department]: ...
    def assess_urgency(self, ctx: DecisionContext) -> Decision[Priority]: ...
    def classify_issue_type(self, ctx: DecisionContext) -> Decision[IssueType]: ...
```
`Decision[T] = {value: T, confidence: float, rationale: str, model: str, raw: dict}`.
Implementations: `llm_structured` (default), `typesafe_jev` (stub raising
`NotImplementedError` with a documented adapter surface), selected via
`DECISION_SERVICE=llm_structured|typesafe_jev`.
If `confidence < decision.min_confidence` (policy file, default 0.6) → escalate to the
department chosen by the fallback table in `routing.yaml`, never silently guess.

### 4.6 Policy engine (`assistant/policy/engine.py` + `config/tenants/nethiz/policy.yaml`)
```yaml
version: 1
tenant: nethiz
decision:
  min_confidence: 0.6
limits:
  max_tool_calls_per_turn: 8
  max_questions_advisory: 5
actions:
  retry_provisioning_job:
    allowed: true
    requires_confirmation: false
    conditions: [{field: job.status, in: [stuck, failed]},
                 {field: job.attempt_count, lt: 3}]
  resend_activation_notification:
    allowed: true
    requires_confirmation: false
    rate_limit: {per_conversation: 2, per_customer_per_day: 5}
  apply_outage_credit:
    allowed: true
    requires_confirmation: true          # irreversible → explicit user consent
    max_amount_try: 50
    conditions: [{field: incident.status, in: [open, monitoring, resolved]},
                 {field: credit.existing_count_30d, lt: 1}]
  reschedule_installation:
    allowed: false
    escalate_to: FIELD_INSTALL
  issue_refund:
    allowed: false
    escalate_to: BILLING
  change_package:
    allowed: false
    escalate_to: SUBSCRIPTION_OPS
  repair_infrastructure:
    allowed: false
    escalate_to: TECHNICAL_INFRA
  cancel_subscription:
    allowed: false
    escalate_to: SUBSCRIPTION_OPS
pii:
  mask_before_model: [national_id, phone, email, address, iban, card_number, full_name]
  allow_unmasked: [customer_no, subscription_id, ticket_key, region_code]
```
API: `engine.check(action_name, context) -> PolicyDecision{allowed, requires_confirmation,
reason_code, reason_tr, escalate_to, limit_applied}`. The model never decides authority:
`ActionExecutor.execute()` calls `check()` first and raises `PolicyDenied` otherwise —
unknown action names default to **deny**. Every check is written to the audit log.

### 4.7 Masking (`assistant/privacy/masking.py`)
`mask_text(text) -> (masked_text, mapping)` and `mask_payload(dict)`.
Rules: TR national id (11 digits) → `***********`; phone (`+90`/`0` + 10 digits) →
`+90 5** *** ** 31` (last two digits kept); email → `a***@d***.com`; IBAN → `TR** **** ...`;
card (13–19 digits, Luhn) → `**** **** **** 1234`; full name → initials (`A** K***`);
address → `<district>, <city>` only. Masking is applied at the **boundary**: everything that
leaves for the model provider, and everything that goes to Langfuse/OTel traces, is masked.
Unmasked values stay in the assistant process only to call company APIs.
`tests/integration/test_no_pii_leak.py` drives a full conversation with a known seeded
customer and asserts no raw national_id/phone/email appears in model payloads, trace spans,
audit evidence or ticket bodies.

### 4.8 Audit log (`assistant/audit/log.py`)
`append(entry)` → hash-chained row (§1.5). `verify_chain(conversation_id)` recomputes hashes.
Every step records: what, why (`reason`), on what data (`evidence.queried_sources` +
`record_ids`), policy decision, and the tool digest. Used by `/api/conversations/{id}/audit`.

### 4.9 Observability (`assistant/observability/tracing.py`)
`openinference-instrumentation-openai-agents` + `langfuse` SDK. `init_tracing()` is a no-op
when `LANGFUSE_ENABLED=false` **or** when the Langfuse host is unreachable (fail-open, logged
once, never raises). Each turn runs inside
`propagate_attributes(user_id=<masked_customer_ref>, session_id=<conversation_id>,
tags=[tenant, mode, chaos_scenario or "none"], metadata={...masked...})`.
Fallback sink when disabled: JSONL at `/app/var/traces/`.

---

## 5. Chaos scenarios (`company/chaos`, `make chaos SCENARIO=...`)

CLI `python -m chaos.cli <scenario> [--customer NH-1000xx] [--region IST-KAD]`,
plus `python -m chaos.cli reset` and `python -m chaos.cli status`.
It only uses the company's own write API/DB (actor `chaos`), never assistant code.

| id | scenario | what it does | expected assistant behaviour |
|---|---|---|---|
| a | `stuck_provisioning` | picks/creates a `provisioning` subscription, sets its job `running` with `heartbeat_at = now()-30m`, `last_error_message='CHAOS_HOLD'` | diagnose stuck job → `retry_provisioning_job` (allowed) → no ticket |
| b | `paid_not_active` | payment `succeeded` but subscription left at `payment_received`, provisioning job missing | small fix allowed (enqueue/retry provisioning + resend notification); if a refund is wanted → BILLING ticket |
| c | `regional_outage` | opens `INC-2026-0xx` (critical) for a region, links every active subscription there, marks modems `offline` | all users in region → one existing incident; **no new per-customer ticket**, attach to incident, inform + optional ≤50 TL credit with confirmation |
| d | `double_charge` | creates a second `succeeded` charge + `payments` row, same amount, 4 min apart | detect duplicate → refund NOT permitted → BILLING ticket with both payment ids |
| e | `missed_installation` | sets yesterday's appointment to `missed`, subscription stays `installation_scheduled` | FIELD_INSTALL ticket with appointment id + team code; no self-reschedule |
| f | `payment_down` | `POST /psp/v1/control {outage:true}` | Prometheus `PaymentGatewayDown` → Alertmanager → assistant proactive diagnosis + TECHNICAL_INFRA ticket + department channel message; user told "ödeme sistemi geçici olarak kullanılamıyor" |

`reset` restores: clears chaos incidents, deletes chaos-created charges/payments/credits,
restores appointment/job/modem/subscription statuses, sets PSP control back to defaults.
`company/chaos/tests/` asserts each scenario's post-state and that `reset` is clean.

---

## 6. Fake data rules

### 6.1 National id (`national_id`)
11 digits, first digit non-zero, generated so the real checksum **fails**
(`company/shared/fake_identity.py: make_invalid_national_id(rng)` — compute valid check digits
then increment the 11th digit by 1 mod 10). A unit test asserts every seeded value fails the
official checksum. Rationale: realistic format, impossible to be a real person's number.

### 6.2 Other
Phones: `+90` + prefix from the reserved-looking set above + 7 digits — never dialable patterns
used by real campaigns. Cards: only `card_token` strings (`tok_test_<n>`) and `card_last4`;
no PAN is ever stored. Emails: `<name>.<surname>@ornek-eposta.test` (`.test` TLD is reserved).
Addresses: real district/city names + fictional street names, no building numbers.

---

## 7. Tests

| Path | What |
|---|---|
| `company/*/tests/` | unit tests per service (lifecycle transitions, scope enforcement, idempotency, ticket transition matrix, webhook signing, seed invariants) |
| `company/chaos/tests/` | scenario post-state + reset |
| `integrations/*/tests/` | tool schema validity, read-only enforcement, error mapping |
| `assistant/*/tests/` | policy matrix, recommendation scoring, masking, audit chain, state machine, decision confidence fallback |
| `tests/architecture/` | import boundaries + forbidden-token scan |
| `tests/integration/` | `test_readonly_role.py`, `test_policy_denial_creates_ticket.py`, `test_regional_incident_no_duplicate_tickets.py`, `test_no_pii_leak.py`, `test_ticket_webhook_closes_loop.py`, `test_langfuse_payload_masked.py` |
| `evals/` | scenario + advisory evaluation, `make eval` |

Runner: `make test` → `docker compose run --rm test-runner pytest <paths>`; the test-runner
image installs `requirements/all.txt` and reaches services by compose DNS. Tests that need a
database use the `*_test` databases created by the fixtures in `tests/conftest.py`.

---

## 8. Environment variables (see `.env.example` for the full list)

Shared: `COMPOSE_PROJECT_NAME`, `TZ=Europe/Istanbul`, `LOG_LEVEL`.
Company: `COMPANY_DB_*`, `CORE_API_KEY_CRM`, `CORE_API_KEY_PARTNER`, `PSP_API_KEY`,
`TICKETING_API_KEY`, `NOTIFY_API_KEY`, `WEBHOOK_SECRET`, `READONLY_DIAG_PASSWORD`,
`PROVISION_SUCCESS_RATE`, `PSP_FAILURE_RATE`, `CREDIT_MAX_PER_REQUEST_TRY`,
`RESEND_COOLDOWN_SECONDS`, `ALERT_INTEGRATION_WEBHOOK_URL`.
Assistant: `ASSISTANT_DB_*`, `TENANT=nethiz`, `LLM_PROVIDER=openai_agents`,
`LLM_MODEL=gpt-4.1-mini`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`,
`DECISION_SERVICE=llm_structured`, `DECISION_MIN_CONFIDENCE`, `MAX_TOOL_CALLS_PER_TURN`,
`MCP_*_URL`, `COMPANY_API_KEY` (= partner key), `DIAG_DATABASE_URL`,
`LANGFUSE_ENABLED`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`,
`EVAL_MODE=scripted|live`.

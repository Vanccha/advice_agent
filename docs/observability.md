# Observability — self-hosted Langfuse

> This file explains what `docker-compose.observability.yml` does. It is kept strictly
> separate from the main stack (`docker-compose.yml`): the `assistant` service does not
> depend on this overlay and keeps working even when the overlay is not running.

## 1. What we run

`docker-compose.observability.yml` runs the same components as Langfuse's official
self-hosting docker-compose file (`https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml`,
**image tag `4`**, the version read on 2026-10-05), with service names adapted to this repo's
naming style. Nothing was invented; every container uses exactly the image of its counterpart
in upstream's own compose file.

| Service (in this repo) | Upstream image | What it does |
|---|---|---|
| `langfuse-db` | `postgres:17` | Langfuse's **own** metadata database: org/project/user/API key/prompt records. It has **no** relation to `company-db` or `assistant-db`; separate container, separate volume (`langfuse-db-data`). |
| `langfuse-clickhouse` | `clickhouse/clickhouse-server:25.12` | Columnar store holding trace/observation/score events (for high-volume analytical queries). |
| `langfuse-redis` | `redis:7` | The worker's BullMQ queue backend (ingestion processing order). |
| `langfuse-minio` | `cgr.dev/chainguard/minio` | S3-compatible object storage: event payloads (`events/`) and media (`media/`) are written here. Upstream does not pin this image to a fixed tag (rolling), and neither do we. |
| `langfuse-worker` | `docker.langfuse.com/langfuse/langfuse-worker:4` | Background worker: writes to ClickHouse, batch exports, ingestion queue. Serves no UI of its own. |
| `langfuse-web` | `docker.langfuse.com/langfuse/langfuse:4` | Langfuse UI + public API (the endpoint we send traces to). |

Every container has a healthcheck (`pg_isready`, ClickHouse `/ping`, `mc ready local`,
`redis-cli ping`, `/api/public/health` and `/api/health` for web/worker), and `langfuse-worker`
and `langfuse-web` **do not start** until ClickHouse + MinIO + Redis + Postgres are
`service_healthy` (`depends_on: condition: service_healthy`) — so `make up-observability`
settles on its own without manual retries.

## 2. Running it

```bash
make up-observability
# == docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

This command starts both the main stack (company + assistant) and Langfuse on the same Docker
network (project name `netswift`), so the `assistant` container can reach
`http://langfuse-web:3000` from inside.

- **Langfuse UI**: `http://localhost:${LANGFUSE_WEB_PORT_HOST:-3000}` (default
  `http://localhost:3000`)
- **Credentials** (`.env.example` → `LANGFUSE_INIT_USER_EMAIL` /
  `LANGFUSE_INIT_USER_PASSWORD`): `demo@example-mail.test` / `demo_password_123`
  (thanks to the `LANGFUSE_INIT_*` variables the org/project/user/API key are created
  automatically on first start — no manual "sign up" is needed for the demo).
- Only three ports are exposed on the host (all via variables in `.env.example`): web `3000`
  (`LANGFUSE_WEB_PORT_HOST`), MinIO S3 API `9090` (`LANGFUSE_MINIO_PORT_HOST`), and Langfuse's
  own Postgres `55434` (`LANGFUSE_DB_PORT_HOST`, for anyone who wants to look with `psql`
  directly; it does not clash with `company-db`'s `55432` or `assistant-db`'s `55433` host
  ports). ClickHouse (`8123`, `9000`), Redis (`6379`) and the MinIO console (`9091`) are bound
  to `127.0.0.1` only; they are not needed for the demo and exist purely for local debugging.
- To stop it (without deleting data): `stop` the relevant containers; do **not** use
  `docker compose down` — other services in this repo may be running alongside.

## 3. How the assistant sends traces

`assistant/observability/` (see `docs/contracts.md` §4.9) automatically instruments OpenAI
Agents SDK calls with `openinference-instrumentation-openai-agents`; this instrumentation
produces OpenTelemetry spans, which flow to Langfuse through the Langfuse SDK v3 OTel bridge:

1. `openinference-instrumentation-openai-agents` emits every agent/tool call as an OTel span.
2. The Langfuse Python SDK v3 `get_client()` call attaches the process's global OTel span
   processor to Langfuse's OTLP endpoint (`LANGFUSE_BASE_URL`).
3. Every chat turn runs inside a `propagate_attributes(user_id=<masked customer reference>,
   session_id=<conversation_id>, tags=[tenant, mode, chaos_scenario or "none"],
   metadata={...masked...})` context, so the right user/session/tag information is attached
   to every trace.

Required Python packages (all **already** in `assistant/requirements.txt` — verified, nothing
to add):

```
langfuse>=3.0
openinference-instrumentation-openai-agents>=0.1
opentelemetry-sdk>=1.29
opentelemetry-exporter-otlp-proto-http>=1.29
openai-agents>=0.2
```

Relevant environment variables (`.env.example`, `docker-compose.yml` → `assistant` service):

| Variable | Meaning |
|---|---|
| `LANGFUSE_ENABLED` | When `false`, `init_tracing()` is a no-op; no OTel/Langfuse calls are made at all. |
| `LANGFUSE_BASE_URL` | Defaults to `http://langfuse-web:3000` (in-container DNS; valid while the overlay is running). |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | The project's API keys. This overlay bootstraps Langfuse with the **same** values (`LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`), so the assistant can send traces from the first start without copying any key by hand. |

## 4. UK GDPR note

`assistant/privacy/masking.py` is applied **before** data reaches a model provider, a trace or
a ticket (see CLAUDE.md rule 8). So no field that reaches Langfuse holds a raw National
Insurance number, phone number, card details etc. — only masked data. Every trace is marked
with these tags so it can be filtered in the UI:

- `tenant` (e.g. `netswift`)
- the masked customer reference (as `user_id`, e.g. not `NS-1000xx` but its masked form)
- `mode` (router / advisory / diagnostic / action)
- `chaos_scenario` (the active scenario's name, or `"none"`)

## 5. Verification

1. Run `make up-observability` and wait 1–3 minutes (Postgres/ClickHouse migrations take a
   while on first start).
2. Open `http://localhost:3000` → sign in with `LANGFUSE_INIT_USER_EMAIL` /
   `LANGFUSE_INIT_USER_PASSWORD` (the auto-created project is already selected).
3. Start a chat with the assistant (`LANGFUSE_ENABLED` must be `true`) → go to the **Traces**
   tab in the Langfuse UI → filter by the `tenant`, masked customer reference, `mode` or
   `chaos_scenario` tags → see the turn's spans (agent steps, tool calls, masked metadata).

## 6. With `LANGFUSE_ENABLED=false` or Langfuse unreachable

`init_tracing()` is **fail-open**: if the Langfuse host cannot be reached (overlay not running,
network trouble, etc.) this is logged once and never raises. The assistant keeps working
normally; traces are then written as JSONL files under `/app/var/traces/` (the local fallback
sink). So the demo never breaks while the Langfuse stack is down.

# CLAUDE.md — NetHız Telekom + AI Support Assistant

This repository is a realistic rehearsal of a commercial product: an **AI status-check agent +
chatbot** that an enterprise buys and bolts onto systems it already runs. To keep the rehearsal
honest, the repo contains **two worlds that must never blend**.

## The two worlds

| | `company/` — NetHız Telekom | `assistant/` — the product |
|---|---|---|
| Story | A mid-size fiber ISP that has been running its own stack for years | A guest system installed from outside |
| Knowledge | Knows nothing about any AI assistant | Knows nothing about NetHız internals |
| Access | Owns its databases and APIs | Reaches the company **only** through `integrations/` |
| Writes | Freely, it owns the data | Never writes to a company database; actions go through the company's own REST API |

`integrations/` is the bridge: one MCP server per company system. **Switching to another
customer must only require changes in `integrations/` and `config/tenants/<name>/`.**

```
company/ ──REST + read-only SQL──> integrations/ (MCP servers) ──MCP──> assistant/
```

## Hard rules (enforced by `tests/architecture/test_boundaries.py`)

1. No file under `assistant/` imports `company`, `shared`, `integrations` or `common`.
2. No file under `company/` imports `assistant` or `integrations`.
3. No file under `company/` may contain the words `assistant`, `advisor`, `chatbot`, `llm`,
   `openai`, `anthropic`, `langfuse`, `mcp` — not in code, SQL, templates or config.
   The Alertmanager webhook that reaches the assistant is an **environment variable**
   (`ALERT_INTEGRATION_WEBHOOK_URL`), i.e. configuration, not code.
4. The assistant never hardcodes a company hostname; adapter URLs come from
   `config/tenants/<tenant>/tenant.yaml` + env.
5. Database writes from the integration layer are impossible by construction: it connects as
   `readonly_diag`, which only has `SELECT` on the `diag.*` views.
6. The model never decides its own authority. `assistant/policy/engine.py` checks
   `config/tenants/<tenant>/policy.yaml` **before** any action; unknown actions are denied.
7. Irreversible actions (money, cancellations) require explicit user confirmation.
8. PII is masked before it reaches a model provider, a trace or a ticket
   (`assistant/privacy/masking.py`).

## Language rule

- Code, identifiers, comments, commit messages, logs: **English**.
- Everything an end user or a department agent reads: **Turkish** (UI strings, assistant
  replies, ticket bodies, policy `*_tr` fields).

## Where things live

```
docs/contracts.md     ← SINGLE SOURCE OF TRUTH: schemas, endpoints, payloads, scenarios.
                        Read it before writing code. Change it before deviating from it.
company/shared/       shared company helpers (db session, api-key auth, metrics, fake data)
company/core-api/     subscriptions, payments, provisioning jobs, incidents, notifications
company/provisioning-worker/   background provisioning job runner
company/payment-gateway-mock/  PSP simulator (failures, refunds, outage switch)
company/ticketing/    Jira-like ticket service + department web panel (/agent)
company/notification-hub/      Teams/Slack stand-in + channel UI
company/monitoring/   prometheus.yml, rules.yml, alertmanager.yml
company/chaos/        failure injection CLI (make chaos SCENARIO=...)
integrations/common/  http client, read-only db access, ToolResult envelope
integrations/mcp_*/   one MCP server per company system
assistant/core_common/     shared types, settings, tenant-config loader, assistant DB models
                      (named core_common, not common: the boundary test forbids the
                       top-level import name `common` inside assistant/)
assistant/policy/     authority engine (policy.yaml interpreter)
assistant/recommendation/  deterministic package scoring (no LLM)
assistant/privacy/    KVKK masking
assistant/audit/      hash-chained, append-only audit log
assistant/decision/   DecisionService interface (llm_structured | typesafe_jev stub)
assistant/llm/        LLMProvider interface (openai_agents | anthropic | scripted)
assistant/modes/      explicit state machine: router / advisory / diagnostic / action
assistant/tickets/    structured ticket builder
assistant/mcp_gateway/  MCP client gateway + action runner (named mcp_gateway, NOT mcp:
                      a package called `mcp` would shadow the installed MCP client SDK)
assistant/observability/  Langfuse + OTel wrapper (no-op when disabled)
assistant/api/, assistant/web/   HTTP API + embedded chat widget
config/tenants/       per-customer configuration (nethiz, _example)
evals/                scenario + advisory evaluations (make eval)
tests/architecture/   boundary enforcement
tests/integration/    cross-service critical flows
```

## Commands

```bash
make up                 # company + assistant, single command
make up-observability   # plus self-hosted Langfuse (docker-compose.observability.yml)
make smoke              # health + seed sanity
make test               # whole suite inside the test-runner container
make chaos SCENARIO=stuck_provisioning
make chaos-reset
make eval
make open               # print demo URLs
```

Demo URLs: chat `http://localhost:8080`, tickets `http://localhost:8003/agent`,
channels `http://localhost:8004`, core API docs `http://localhost:8001/docs`,
Prometheus `http://localhost:9091`, Alertmanager `http://localhost:9093`.

## Stack decisions

- Python 3.12 + FastAPI + PostgreSQL 16 everywhere. **.NET + MS SQL Server was rejected**
  because the host is arm64 and the SQL Server image is amd64-only (emulation is slow and
  flaky) — the fallback allowed by the project brief.
- UIs are server-rendered Jinja2 + vanilla JS: no Node toolchain, no build step.
- Tests run inside containers (`python:3.12-slim`), so the host Python version is irrelevant.
- Default model provider is the OpenAI Agents SDK; `anthropic` and `scripted` implement the
  same `LLMProvider` interface. Evaluations default to `scripted` so they need no API key.

## Working agreements

- Each service owns its directory. Do not edit another service's files; if a contract needs
  to change, update `docs/contracts.md` first and say so.
- Every service exposes `/health` and `/metrics`.
- Every mutating company endpoint requires `X-API-Key` and an explicit scope.
- Add unit tests next to the code (`<service>/tests/`), integration tests in
  `tests/integration/`.
- Keep `docs/contracts.md`, this file and `README.md` current when behaviour changes.

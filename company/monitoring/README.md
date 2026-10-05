# Monitoring — Prometheus + Alertmanager

Prometheus (`:9091` on the host) scrapes every company service's `/metrics` every 10s and
evaluates `rules.yml`. Firing/resolved alerts go to Alertmanager (`:9093`), which fans each
one out to two receivers (`continue: true` on both, see `alertmanager.yml`):

1. **`nethiz-channels`** — posts into `notification-hub` (the Teams/Slack stand-in,
   `http://localhost:8004`) so a human operator sees it in the right department channel.
2. **`integration-webhook`** — the company's own configured third-party integration
   endpoint (deployment configuration, see the comment in `alertmanager.yml`).

## Alerts (`rules.yml`)

| Alert | Meaning | Department / channel | `for` |
|---|---|---|---|
| `PaymentGatewayDown` | `payment-gateway` is down or the PSP is in simulated outage | TECHNICAL_INFRA → `teknik-altyapi` | 1m |
| `CoreApiDown` | `core-api` is not responding to health checks | TECHNICAL_INFRA → `teknik-altyapi` | 1m |
| `StuckProvisioningJobs` | At least one provisioning job has been stuck | SUBSCRIPTION_OPS → `abonelik-islemleri` | 2m |
| `HighPaymentFailureRate` | Over half of recent charge attempts failed | BILLING → `faturalama` | 3m |
| `RegionalOutageDetected` | An open, critical-severity regional incident exists | TECHNICAL_INFRA → `teknik-altyapi` | 1m |
| `MissedInstallations` | A field-install appointment was missed in the last 24h | FIELD_INSTALL → `saha-kurulum` | 5m |

Any alert whose `department` label notification-hub does not recognise falls back to the
`operasyon-genel` channel.

## How to check it

- Prometheus rule/target status: `http://localhost:9091/alerts` and
  `http://localhost:9091/targets` (some targets may read as "down" while other services in
  this multi-service project are still being built — that's expected and not a config error).
- Alertmanager UI: `http://localhost:9093` (active alerts, silences, routing tree).
- Department channels: `http://localhost:8004` (list) and `http://localhost:8004/c/<slug>`
  (stream), e.g. `http://localhost:8004/c/teknik-altyapi`.
- Config sanity without restarting anything:
  `docker compose logs prometheus alertmanager` — both log a clean config load on startup;
  a bad YAML file or rule expression shows up there immediately.

## Files

- `prometheus.yml` — scrape jobs (`core-api`, `provisioning-worker`, `payment-gateway`,
  `ticketing`, `notification-hub`, all on `:8000/metrics`), rule file, Alertmanager target.
- `rules.yml` — the six alerts above.
- `alertmanager.yml` — routing tree + the two webhook receivers.

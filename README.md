# Gateway — Integration Platform

In-house integration platform: **Kong** (DB-less) → **Redpanda** (3-node Kafka cluster) → **n8n** (queue-mode workflow orchestration), backed by Postgres + Redis and a full Prometheus / Grafana / Loki observability stack.

**Integrations:** Samsara • NetSuite (OAuth1 TBA) • Unigroup Converge (OAuth2 + GraphQL) • WMS • Custom APIs

> **Status:** HA-ready stack (3-broker Redpanda, queue-mode n8n workers, shared
> rate-limit counters) that runs on a single host via `docker-compose` for
> staging / small-scale production. **True HA requires ≥3 hosts**: on a single
> host the box itself is a SPOF regardless of replication. Review
> [`docs/SECURITY.md`](docs/SECURITY.md) and
> [`docs/DEPLOYMENT-CHECKLIST.md`](docs/DEPLOYMENT-CHECKLIST.md) before
> pointing real traffic at any instance you don't control end-to-end.

---

## Contents

- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Services](#services)
- [Configuration](#configuration)
- [Scaling](#scaling)
- [Security posture](#security-posture)
- [Observability](#observability)
- [Operations](#operations)
- [Repo layout](#repo-layout)

---

## Architecture

```mermaid
graph LR
  EXT[External systems] -->|HTTPS 443| KONG[Kong Gateway<br/>DB-less, key-auth + HMAC<br/>rate-limit via Redis]
  KONG -->|internal| N8N[n8n main<br/>webhooks + API]
  N8N -->|enqueue| REDIS[(Redis<br/>queue broker)]
  REDIS --> WORK[n8n-worker × N]
  WORK --> RP[Redpanda<br/>3-broker cluster<br/>RF=3, min.insync=2]
  RP --> WORK
  WORK --> NS[NetSuite]
  WORK --> WMS[WMS]
  WORK --> EDI[Unigroup EDI]
  N8N --> PG[(Postgres)]
  WORK --> PG

  subgraph Observability
    PROM[Prometheus] --> AM[Alertmanager]
    PROM --> GRAF[Grafana]
    LOKI[Loki] --> GRAF
    PROMTAIL[Promtail] --> LOKI
  end
  KONG --> PROM
  N8N --> PROM
  RP --> PROM
  PG --> PROM
  REDIS --> PROM
```

Key properties:

- **Kong is DB-less.** All routes, plugins, consumers, and API keys live in [`kong/kong.yml`](kong/kong.yml). There is no `kong-database` container.
- **Redpanda is a 3-broker cluster.** Topics are created with `replication=3, min.insync.replicas=2, compression=zstd`.
- **n8n runs in queue mode.** The main container handles webhooks/API; execution is dispatched via Redis to N worker containers that you can scale independently.
- **Admin UIs are loopback-only.** Kong admin (8001/8002), n8n (5678), Redpanda Console (8080), Grafana (3002), Prometheus (9090), Alertmanager (9093), and Loki (3100) bind to `127.0.0.1` on the host. Only the public proxy on 8000/8443 listens on `0.0.0.0`.
- **Every public route is auth'd and rate-limited.** `key-auth` + redis-backed `rate-limiting` + `request-size-limiting` + `cors` + security headers + `ip-restriction` + `correlation-id` are applied globally; Samsara additionally has an HMAC pre-function plugin.

---

## Quick start

```bash
cp .env.example .env               # then replace every CHANGE_ME
./scripts/setup.sh                 # generates dev TLS cert, starts stack, creates topics
./scripts/test.sh                  # smoke test
```

`setup.sh` will:
1. Verify Docker.
2. Generate `config/kong/certs/server.{crt,key}` (self-signed, for dev only).
3. `docker compose up -d --wait`.
4. Create Redpanda topics with production settings.

To scale workers: `docker compose up -d --scale n8n-worker=4`.

Edit `kong/kong.yml` → apply with `./scripts/kong-setup.sh` (hot reload via loopback admin API).

---

## Services

| Service           | Port (exposed)      | Role                                   |
|-------------------|---------------------|----------------------------------------|
| Kong proxy        | `0.0.0.0:8000/8443` | **Public** - HTTPS API gateway         |
| Kong admin        | `127.0.0.1:8001`    | Admin API (tunnel to reach)            |
| Kong manager      | `127.0.0.1:8002`    | Web UI                                 |
| n8n main          | `127.0.0.1:5678`    | Workflow editor + webhook frontend     |
| n8n-worker        | –                   | Queue workers (replicas configurable)  |
| Postgres          | internal            | n8n state                              |
| Redis             | internal            | Queue broker + Kong rate-limit counters|
| Redpanda 0/1/2    | internal (`:9092` on node-0 loopback) | Kafka cluster            |
| Redpanda Console  | `127.0.0.1:8080`    | Topic / message inspector              |
| Prometheus        | `127.0.0.1:9090`    | Metrics                                |
| Alertmanager      | `127.0.0.1:9093`    | Alert routing (Slack / PagerDuty)      |
| Grafana           | `127.0.0.1:3002`    | Dashboards + log explorer              |
| Loki              | `127.0.0.1:3100`    | Log aggregation                        |
| Promtail          | internal            | Docker log shipping → Loki             |
| node-exporter     | internal            | Host metrics                           |
| cAdvisor          | internal            | Container metrics                      |
| postgres-exporter | internal            | Postgres metrics                       |
| redis-exporter    | internal            | Redis metrics                          |

To reach a loopback-bound UI from a workstation:
```bash
ssh -L 3002:127.0.0.1:3002 -L 8001:127.0.0.1:8001 -L 5678:127.0.0.1:5678 prod-host
```

---

## Configuration

All tunables are environment variables consumed by `docker-compose.yml`:

- **Application secrets** → `.env` (gitignored). In production inject via AWS Secrets Manager / Vault / SSM — the compose file already reads `${VAR}` so this is a drop-in.
- **Kong routes / plugins / consumers** → `kong/kong.yml`.
- **Prometheus scrape + rules** → `config/prometheus/prometheus.yml`, `config/prometheus/rules/*.yml`.
- **Alert routing** → `config/alertmanager/alertmanager.yml` (uses `ALERTMANAGER_SLACK_WEBHOOK`, `ALERTMANAGER_PAGERDUTY_KEY`).
- **Log pipeline** → `config/loki/loki-config.yaml`, `config/promtail/promtail.yml`.
- **Grafana datasources / dashboards** → `config/grafana/provisioning/`.
- **Kafka topic layout** → `scripts/create-topics.sh`.

### Rotating an API key

1. Edit the relevant consumer block in `kong/kong.yml` (replace the `key:` value).
2. `./scripts/kong-setup.sh` (hot reload).
3. Distribute the new key to the caller.

### Adding a route

Append a new `services:` entry in `kong/kong.yml` with its own `routes:` — the global plugin block will auto-apply auth + rate-limit + size + cors + headers + ip-restriction. Then `./scripts/kong-setup.sh`.

---

## Scaling

- **Kong**: stateless in DB-less mode. Run ≥ 2 replicas behind an L4/L7 LB. `docker compose up -d --scale kong=N`.
- **n8n workers**: `docker compose up -d --scale n8n-worker=N`. Each worker processes up to `--concurrency=10` jobs in parallel.
- **Redpanda**: increase partition counts in `scripts/create-topics.sh` and scale brokers horizontally. `samsara-events` defaults to 24 partitions keyed on `vehicle_id`.
- **Postgres**: migrate to a managed service (RDS / CloudSQL) before significant write load.

---

## Security posture

Summary (full details in [`docs/SECURITY.md`](docs/SECURITY.md)):

- Admin surfaces bound to loopback only.
- Kong DB-less + declarative → no runtime admin-API writes in prod.
- `key-auth` + redis-shared `rate-limiting` + `request-size-limiting` + HSTS/CSP + `ip-restriction` on every route.
- Samsara HMAC verified in a Kong `pre-function` before n8n ever sees the request.
- PII (driver names, formatted addresses) redacted before publish to Kafka.
- `N8N_ENCRYPTION_KEY` set so n8n credentials are encrypted at rest.
- `scram-sha-256` on Postgres.
- TLS-only routes; modern cipher suite.
- gitleaks + Trivy (config/fs/image) in CI as **blocking** gates.
- Backups encrypted with `age` when `BACKUP_AGE_RECIPIENT` is set.

---

## Observability

- **Metrics**: Prometheus scrapes Kong, n8n, Redpanda (all 3 nodes), Postgres, Redis, node-exporter, cAdvisor, Alertmanager, Loki.
- **Alerts**: `config/prometheus/rules/gateway.rules.yml` covers 5xx rate, p95 latency, consumer lag, under-replicated partitions, queue backlog, workflow error rate, disk/memory, container restart loops, Postgres/Redis down.
- **Logs**: Promtail discovers every container via the Docker socket and ships to Loki with `correlation-id` extraction.
- **Dashboards**: provisioned from `config/grafana/dashboards/`.

Slack + PagerDuty receivers are wired in `config/alertmanager/alertmanager.yml`; populate `ALERTMANAGER_SLACK_WEBHOOK` / `ALERTMANAGER_PAGERDUTY_KEY` in `.env`.

---

## Operations

```bash
# Preflight
./scripts/validate-config.sh

# Daily / CI
./scripts/test.sh

# Backups (encrypted if BACKUP_AGE_RECIPIENT set; prunes > BACKUP_RETENTION_DAYS)
./scripts/backup.sh
./scripts/restore.sh ./backups/<ts>

# Redpanda
docker exec gateway-redpanda-0 rpk cluster health --brokers redpanda-0:29092
docker exec gateway-redpanda-0 rpk topic describe samsara-events --brokers redpanda-0:29092
docker exec gateway-redpanda-0 rpk group list --brokers redpanda-0:29092

# Kong hot reload
./scripts/kong-setup.sh

# Scale
docker compose up -d --scale n8n-worker=4 --scale kong=2
```

A cron template for daily backups is provided at `scripts/cron/gateway-crontab`.

---

## Repo layout

```
.
├── docker-compose.yml               # Full HA topology
├── kong/kong.yml                    # DB-less declarative Kong config
├── config/
│   ├── kong/certs/                  # TLS (self-signed for dev; real certs in prod)
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── rules/gateway.rules.yml
│   ├── alertmanager/alertmanager.yml
│   ├── grafana/
│   │   ├── provisioning/{datasources,dashboards}/
│   │   └── dashboards/
│   ├── loki/loki-config.yaml
│   └── promtail/promtail.yml
├── workflows/                       # n8n workflow exports
├── scripts/
│   ├── setup.sh                     # first-time setup (TLS cert + up)
│   ├── test.sh                      # smoke test
│   ├── validate-config.sh           # preflight
│   ├── create-topics.sh             # partitions=N, RF=3, min.insync=2, zstd
│   ├── kong-setup.sh                # hot-reload kong/kong.yml
│   ├── backup.sh                    # age-encrypted; retention-enforced
│   ├── restore.sh
│   └── cron/gateway-crontab
├── docs/
│   ├── SECURITY.md                  # controls in place + pre-prod checklist
│   ├── DEPLOYMENT-CHECKLIST.md
│   ├── getting-started.md
│   ├── kong-plugins.md              # reference
│   ├── netsuite-integration.md
│   ├── samsara-integration.md
│   └── workflow-resilience.md
└── .github/workflows/ci.yml         # gitleaks + Trivy + smoke test
```

---

## License

Internal - all rights reserved.

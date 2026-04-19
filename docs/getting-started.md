# Getting Started

## Prerequisites

- Docker (Engine 20.10+) with Compose V2
- `openssl` (to generate the local-dev TLS cert)
- `age` (optional, for encrypted backups): `brew install age`

## First-time setup

```bash
cp .env.example .env           # then edit .env - replace every CHANGE_ME
./scripts/setup.sh             # generates dev TLS cert, starts stack, creates topics
./scripts/test.sh              # smoke test
```

`setup.sh` does four things:

1. Validates Docker is running.
2. Generates a self-signed TLS cert into `config/kong/certs/` (dev only — replace in prod).
3. Runs `docker compose up -d --wait`.
4. Creates Redpanda topics with `replication_factor=3`, `min.insync.replicas=2`.

## Quick links

- NetSuite already has API keys and need fast activation: [5-Minute Activation Checklist](./netsuite-integration.md#5-minute-activation-checklist-if-you-already-have-keys)
- Unigroup Converge setup: [`docs/unigroup-integration.md`](./unigroup-integration.md) — OAuth2 + GraphQL, not EDI.
- Admin UI (unified credential management + audit + rotate-with-healthcheck): [`admin-ui/README.md`](../admin-ui/README.md)
- Zammad helpdesk wiring (critical alerts → tickets, with 5 false-positive filters): [`docs/alerting-to-zammad.md`](./alerting-to-zammad.md)
- Replay a Samsara webhook through the full funnel locally: `./scripts/samsara-replay.sh` (see [Testing a route](#testing-a-route) below)

## Architecture at a glance

```
Internet ──► Kong (443, key-auth + HMAC + rate-limit) ──► n8n main
                                                           └──► n8n-worker × N ──► Redpanda (3 brokers)
                                                                                    │
                                                                                    └──► n8n consumers ──► NetSuite / WMS / …
Observability: Prometheus + Alertmanager + Grafana + Loki + Promtail + node_exporter + cAdvisor
```

Kong runs in **DB-less mode**. Routes, plugins, consumers and API keys live in `kong/kong.yml`. Edit that file, then:

```bash
docker compose restart kong          # or
./scripts/kong-setup.sh              # hot-reload via admin API on 127.0.0.1:8001
```

## Endpoints

**Public (via Kong):**
- `POST https://<host>/samsara`   — requires `X-API-Key` + `X-Samsara-Signature` (HMAC SHA-256 of body)
- `POST https://<host>/netsuite`  — requires `X-API-Key`. Inbound from NetSuite User Event scripts; see [`netsuite-integration.md`](./netsuite-integration.md).
- `POST https://<host>/wms`       — requires `X-API-Key`. Inbound reserved (outbound flows via Kafka `wms-out`).
- `POST https://<host>/unigroup`  — requires `X-API-Key`. Webhook scaffolding; primary Unigroup flow is outbound via Kafka `unigroup-out`.
- `POST https://<host>/dispatch`  — requires `X-API-Key`. Inbound reserved (outbound flows via Kafka `dispatch-out`).

**Admin UIs (bound to `127.0.0.1` only; use an SSH tunnel from your workstation):**

| Service        | URL                         | Notes                                         |
|----------------|-----------------------------|-----------------------------------------------|
| Kong Admin     | http://127.0.0.1:8001       |                                               |
| Kong Manager   | http://127.0.0.1:8002       |                                               |
| n8n            | http://127.0.0.1:5678       | basic auth from `.env`                        |
| Redpanda UI    | http://127.0.0.1:8080       |                                               |
| Grafana        | http://127.0.0.1:3002       | basic auth from `.env`                        |
| Prometheus     | http://127.0.0.1:9090       |                                               |
| Alertmanager   | http://127.0.0.1:9093       |                                               |
| Loki           | http://127.0.0.1:3100       |                                               |
| Admin UI       | http://127.0.0.1:7070       | opt-in: `docker compose --profile admin up -d admin-ui` |

SSH tunnel example:
```bash
ssh -L 8001:127.0.0.1:8001 -L 5678:127.0.0.1:5678 -L 3002:127.0.0.1:3002 prod-host
```

## Testing a route

The fastest way is `./scripts/samsara-replay.sh` — it signs a canned
payload with `SAMSARA_WEBHOOK_SECRET` and POSTs it through Kong,
exercising key-auth + HMAC + rate-limit + n8n + Kafka in one shot. Same
script runs as part of CI.

```bash
./scripts/samsara-replay.sh                # expect HTTP 202
TAMPER=1 ./scripts/samsara-replay.sh       # expect HTTP 401 (HMAC reject)

# Confirm the event made it all the way to Kafka:
docker exec gateway-redpanda-0 rpk topic consume samsara-events \
  --brokers redpanda-0:29092 -n 1 -f '%v\n'
```

If you need to hand-craft the request (e.g. from an external host):

```bash
BODY='{"eventType":"GeofenceEntry","data":{"vehicle":{"id":"v1"}}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SAMSARA_WEBHOOK_SECRET" -hex | awk '{print $NF}')

curl -sk https://<host>/samsara \
  -H "X-API-Key: $SAMSARA_API_KEY" \
  -H "X-Samsara-Signature: $SIG" \
  -H "Content-Type: application/json" \
  --data-binary "$BODY"
```

## Scaling n8n workers

```bash
docker compose up -d --scale n8n-worker=4
```

Workers pull jobs from Redis (queue mode). The main `n8n` container only handles
the web/API/webhook frontends.

## Backups

```bash
./scripts/backup.sh                   # encrypted with age (if BACKUP_AGE_RECIPIENT set)
./scripts/restore.sh ./backups/<ts>   # decrypt + restore
```

A cron template is provided at `scripts/cron/gateway-crontab`.

## Alerting — wire destinations

The stack comes with Prometheus + Alertmanager + rules already firing.
They just need somewhere to send things. Pick any or all:

**Slack** (real-time, all severities) — set `ALERTMANAGER_SLACK_WEBHOOK`.
**PagerDuty** (phone/page on critical) — set `ALERTMANAGER_PAGERDUTY_KEY`.
**Zammad** (auto-create helpdesk tickets from critical alerts, with
false-positive filtering):

1. **Mint a Zammad token**: in Zammad go to *Profile → Token Access →
   Create Token*. Permission: `ticket.agent`. The token is shown once;
   copy it.
2. **Drop into `.env`**:
   ```bash
   ZAMMAD_URL=https://helpdesk.goarmstrong.com
   ZAMMAD_API_TOKEN=<token>
   ZAMMAD_GROUP=Users              # or whichever Zammad group tickets go in
   ZAMMAD_CUSTOMER=info@goarmstrong.com
   docker compose up -d n8n n8n-worker
   ```
3. **Import the workflow** into n8n at `http://127.0.0.1:5678` via
   *Workflows → Import from File* → `workflows/alertmanager-to-zammad.json`.
   Activate it.
4. **Verify** with the curl test in
   [`docs/alerting-to-zammad.md`](./alerting-to-zammad.md) — first call
   creates a ticket, second call appends an article (dedup working).

Only `severity: critical` alerts become tickets. Warnings stay on Slack.
Dedup (sha256 fingerprint), grouping (Alertmanager), and minimum-firing-
duration (`for: ≥5m` on every rule) combine to keep the helpdesk clean.
Full walkthrough + tuning guide in [`docs/alerting-to-zammad.md`](./alerting-to-zammad.md).

## Operational cheatsheet

```bash
docker compose ps                                # status
docker compose logs -f kong n8n                  # tail
docker compose restart kong                      # reload kong/kong.yml
./scripts/test.sh                                # smoke test (use SMOKE_API_KEY for rate-limit probe)
./scripts/samsara-replay.sh                      # full-funnel replay of a canned Samsara event
./scripts/validate-config.sh                     # preflight
docker exec gateway-redpanda-0 rpk cluster health --brokers redpanda-0:29092

# Credential rotation (with healthcheck + auto-rollback)
docker compose --profile admin up -d admin-ui
open http://127.0.0.1:7070                       # Credentials tab -> Edit / Rotate
```

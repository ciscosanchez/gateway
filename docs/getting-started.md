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
- `POST https://<host>/netsuite`  — requires `X-API-Key`
- `POST https://<host>/wms`       — requires `X-API-Key`
- `POST https://<host>/unigroup`  — requires `X-API-Key`

**Admin UIs (bound to `127.0.0.1` only; use an SSH tunnel from your workstation):**

| Service        | URL                         |
|----------------|-----------------------------|
| Kong Admin     | http://127.0.0.1:8001        |
| Kong Manager   | http://127.0.0.1:8002        |
| n8n            | http://127.0.0.1:5678        |
| Redpanda UI    | http://127.0.0.1:8080        |
| Grafana        | http://127.0.0.1:3002        |
| Prometheus     | http://127.0.0.1:9090        |
| Alertmanager   | http://127.0.0.1:9093        |
| Loki           | http://127.0.0.1:3100        |

SSH tunnel example:
```bash
ssh -L 8001:127.0.0.1:8001 -L 5678:127.0.0.1:5678 -L 3002:127.0.0.1:3002 prod-host
```

## Testing a route

Rotate the placeholder API keys in `kong/kong.yml` first, then:

```bash
# HMAC-sign a Samsara test payload
BODY='{"eventType":"GeofenceEntry","data":{"vehicle":{"id":"v1"}}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SAMSARA_WEBHOOK_SECRET" -hex | awk '{print $2}')

curl -sk https://localhost:8443/samsara \
  -H "X-API-Key: $SAMSARA_API_KEY" \
  -H "X-Samsara-Signature: $SIG" \
  -H "Content-Type: application/json" \
  -d "$BODY"
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

## Operational cheatsheet

```bash
docker compose ps                                # status
docker compose logs -f kong n8n                  # tail
docker compose restart kong                      # reload kong/kong.yml
./scripts/test.sh                                # smoke test
./scripts/validate-config.sh                     # preflight
docker exec gateway-redpanda-0 rpk cluster health --brokers redpanda-0:29092
```

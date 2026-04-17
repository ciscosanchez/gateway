# Gateway Stack — Status Snapshot

> This file is a one-page view of the default bring-up. It's regenerated after
> each `./scripts/setup.sh`. Treat it as a **snapshot of a local dev instance**,
> not a source of truth for prod. For production, see
> [`docs/DEPLOYMENT-CHECKLIST.md`](docs/DEPLOYMENT-CHECKLIST.md).

## What should be running after `./scripts/setup.sh`

| Component            | Reachable at                      | Auth                         |
|----------------------|-----------------------------------|------------------------------|
| Kong public proxy    | `https://localhost:8443`          | `X-API-Key` required         |
| Kong admin           | `http://127.0.0.1:8001`            | none (loopback only)         |
| Kong manager         | `http://127.0.0.1:8002`            | none (loopback only)         |
| n8n (main)           | `http://127.0.0.1:5678`            | basic auth from `.env`       |
| n8n worker           | n/a                               | –                            |
| Redpanda brokers     | 3 internal nodes; `127.0.0.1:9092` published from node-0 | – |
| Redpanda Console     | `http://127.0.0.1:8080`            | none (loopback only)         |
| Postgres             | internal                          | scram-sha-256                |
| Redis                | internal                          | `REDIS_PASSWORD`             |
| Prometheus           | `http://127.0.0.1:9090`            | none (loopback only)         |
| Alertmanager         | `http://127.0.0.1:9093`            | none (loopback only)         |
| Grafana              | `http://127.0.0.1:3002`            | basic auth from `.env`       |
| Loki                 | `http://127.0.0.1:3100`            | none (loopback only)         |

Nothing except `8000` and `8443` listens on `0.0.0.0`.

## Public routes (defined in `kong/kong.yml`)

All HTTPS-only, all require `X-API-Key`, all rate-limited.

| Route           | Upstream               | Extra          |
|-----------------|------------------------|----------------|
| `POST /samsara` | `http://n8n:5678/webhook/samsara`  | HMAC SHA-256 via `X-Samsara-Signature` |
| `POST /netsuite`| `http://n8n:5678/webhook/netsuite` | —              |
| `POST /wms`     | `http://n8n:5678/webhook/wms`      | —              |
| `POST /unigroup`| `http://n8n:5678/webhook/unigroup` | —              |

## Kafka topics (see `scripts/create-topics.sh`)

All topics created with **replication=3, min.insync.replicas=2, compression=zstd**.

| Topic              | Partitions | Retention | Notes                              |
|--------------------|------------|-----------|------------------------------------|
| `samsara-events`   | 24         | 7 d       | Key by `vehicle_id` for ordering   |
| `orders`           | 12         | 30 d      | Drives NetSuite sales-order flow   |
| `inventory`        | 6          | 7 d       | Stock deltas                       |
| `edi-outbound`     | 6          | 30 d      | EDI 850/856 for Unigroup           |
| `netsuite-updates` | 12         | 7 d       | Confirmations from NetSuite        |
| `wms-events`       | 6          | 7 d       | WMS                                |
| `errors-dlq`       | 3          | 90 d      | Dead-letter queue                  |

## First checks

```bash
./scripts/test.sh                              # smoke test
./scripts/validate-config.sh                   # preflight
docker compose ps                              # container health
docker exec gateway-redpanda-0 rpk cluster health --brokers redpanda-0:29092
```

## What you should do right now (if you haven't)

1. **Open `.env`** and replace every `REPLACE_ME` / `CHANGE_ME`.
2. **Rotate the Samsara token** in the Samsara console if it was ever in a committed `.env`.
3. **Edit `kong/kong.yml`** — replace every `REPLACE_ME_*_api_key`, tighten `ip-restriction.allow` and `cors.origins`.
4. **Set `BACKUP_AGE_RECIPIENT`** so `./scripts/backup.sh` encrypts output.
5. **Set `ALERTMANAGER_*`** so alerts route somewhere real.
6. Read [`docs/SECURITY.md`](docs/SECURITY.md) and walk [`docs/DEPLOYMENT-CHECKLIST.md`](docs/DEPLOYMENT-CHECKLIST.md).

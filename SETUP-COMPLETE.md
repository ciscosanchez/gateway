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
| Admin UI *(opt-in)*  | `http://127.0.0.1:7070`            | HTTP Basic when `ADMIN_UI_USER` / `ADMIN_UI_PASSWORD` set |

Nothing except `8000` and `8443` listens on `0.0.0.0`.

The admin UI is profile-gated — bring it up with `docker compose --profile admin up -d admin-ui`.
See [`admin-ui/README.md`](admin-ui/README.md).

## Public routes (defined in `kong/kong.yml`)

All HTTPS-only, all require `X-API-Key`, all rate-limited.

| Route           | Upstream               | Extra          |
|-----------------|------------------------|----------------|
| `POST /samsara` | `http://n8n:5678/webhook/samsara`  | HMAC SHA-256 via `X-Samsara-Signature` |
| `POST /netsuite`| `http://n8n:5678/webhook/netsuite` | NetSuite User Event script posts here |
| `POST /wms`     | `http://n8n:5678/webhook/wms`      | — (inbound reserved)                  |
| `POST /unigroup`| `http://n8n:5678/webhook/unigroup` | — (inbound scaffolding, see Converge docs) |
| `POST /dispatch`| `http://n8n:5678/webhook/dispatch` | — (inbound reserved)                  |

## Kafka topics (see `scripts/create-topics.sh`)

All topics created with **replication=3, min.insync.replicas=2, compression=zstd**.

| Topic              | Partitions | Retention | Notes                              |
|--------------------|------------|-----------|------------------------------------|
| `samsara-events`   | 24         | 7 d       | Key by `vehicle_id` for ordering   |
| `orders`           | 12         | 30 d      | Drives NetSuite sales-order flow   |
| `inventory`        | 6          | 7 d       | Stock deltas                       |
| `netsuite-updates` | 12         | 7 d       | Confirmations / NetSuite change feed |
| `unigroup-out`     | 6          | 30 d      | Outbound requests to Unigroup Converge (GraphQL + docs) |
| `unigroup-in`      | 6          | 7 d       | Unigroup responses + polled updates |
| `wms-events`       | 6          | 7 d       | Inbound WMS events                 |
| `wms-out`          | 6          | 30 d      | Outbound requests to WMS REST API  |
| `wms-updates`      | 6          | 7 d       | WMS responses                      |
| `dispatch-out`     | 6          | 30 d      | Outbound requests to Dispatch      |
| `dispatch-updates` | 6          | 7 d       | Dispatch responses                 |
| `edi-outbound`     | 6          | 30 d      | Legacy — will retire once all flows migrate off true-EDI |
| `errors-dlq`       | 3          | 90 d      | Dead-letter queue                  |

## First checks

```bash
./scripts/test.sh                              # smoke test
./scripts/validate-config.sh                   # preflight
docker compose ps                              # container health
docker exec gateway-redpanda-0 rpk cluster health --brokers redpanda-0:29092
```

## What you should do right now (if you haven't)

1. **Open `.env`** and replace every `REPLACE_ME` / `CHANGE_ME` /
   `dev_samsara_webhook_secret_*`.
2. **Rotate the Samsara token** in the Samsara console if it was ever in a committed `.env`.
3. **Edit `kong/kong.yml`** — replace every `REPLACE_ME_*_api_key`, tighten
   `ip-restriction.allow` and `cors.origins`. Or use the admin UI —
   `docker compose --profile admin up -d admin-ui`, visit
   `http://127.0.0.1:7070`, rotate through the Credentials tab.
4. **Set `ADMIN_UI_USER` / `ADMIN_UI_PASSWORD`** before enabling the admin profile
   (otherwise the UI is unauthenticated).
5. **Set `BACKUP_AGE_RECIPIENT`** so `./scripts/backup.sh` encrypts output.
6. **Wire alerting destinations**:
   - `ALERTMANAGER_SLACK_WEBHOOK` for real-time visibility (all severities).
   - `ALERTMANAGER_PAGERDUTY_KEY` for paging on critical.
   - `ZAMMAD_URL` + `ZAMMAD_API_TOKEN` + `ZAMMAD_GROUP` + `ZAMMAD_CUSTOMER`
     to auto-create tickets from critical alerts. Mint the token under
     *Profile → Token Access* in Zammad (`ticket.agent` perm). Then
     **import `workflows/alertmanager-to-zammad.json`** into n8n
     (*Workflows → Import from File → Activate*). Five false-positive
     filters keep the helpdesk clean; walkthrough in
     [`docs/alerting-to-zammad.md`](docs/alerting-to-zammad.md).
7. Read [`docs/SECURITY.md`](docs/SECURITY.md) and walk [`docs/DEPLOYMENT-CHECKLIST.md`](docs/DEPLOYMENT-CHECKLIST.md).

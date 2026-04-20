# Deployment Checklist

## Pre-deploy

### Configuration
- [ ] `.env` populated with real secrets pulled from the secret manager (no `CHANGE_ME`/`REPLACE_ME`).
- [ ] `config/kong/certs/server.{crt,key}` are production certs (not self-signed).
- [ ] `kong/kong.yml`:
  - [ ] Every `REPLACE_ME_*_api_key` replaced with a rotated key.
  - [ ] `ip-restriction.allow` pinned to real caller CIDRs.
  - [ ] `cors.origins` pinned to real origins.
- [ ] `ALERTMANAGER_SLACK_WEBHOOK` and `ALERTMANAGER_PAGERDUTY_KEY` set.
- [ ] `BACKUP_AGE_RECIPIENT` set so backups are encrypted.

### Infrastructure
- [ ] Kong running behind an LB on 443 only (no direct host binding in prod).
- [ ] ≥ 2 Kong replicas (DB-less, stateless).
- [ ] Postgres on managed service with TLS, daily backups, PITR.
- [ ] Redpanda 3-node cluster on 3 separate hosts (or Redpanda Cloud / MSK).
- [ ] Redis reachable only on internal network.
- [ ] n8n main + ≥ 2 workers in queue mode (`docker compose --profile ha up -d --scale n8n-worker=2+`). The `n8n-worker` service only exists under the `ha` profile; without it, n8n runs single-process (the default baseline of ~240 rps — see [`performance.md`](performance.md)).
- [ ] On a fresh host: `./scripts/n8n-bootstrap.sh` has been run post-deploy so webhooks are actually registered. A workflow whose `active` flag is true in the DB but which has no row in `webhook_entity` silently 404s — this is how the n8n CLI leaves it.

### Security
- [ ] Admin UIs (8001/8002/5678/8080/9090/9093/3002/3100) are not reachable from the public internet.
- [ ] TLS certs valid (`openssl x509 -in server.crt -noout -dates`).
- [ ] `./scripts/validate-config.sh` passes with 0 errors.
- [ ] CI pipeline is green (gitleaks + Trivy gates passing).

### Observability
- [ ] Prometheus scraping all 9 targets (`/targets` page - all `up=1`).
- [ ] Alertmanager receiver test fires in Slack / PagerDuty.
- [ ] Grafana dashboards loaded (provisioned from `config/grafana/dashboards`).
- [ ] Loki receiving logs (`{container="gateway-kong"}` query returns lines).

### Data
- [ ] Topics created with replication=3 (`rpk topic describe samsara-events`).
- [ ] Consumer groups created (`rpk group list`).
- [ ] Backup + restore drill completed on staging within the last 30 days.

---

## Deploy

```bash
# Validate
./scripts/validate-config.sh

# Snapshot current state
./scripts/backup.sh

# Tag
git tag -a v1.0.0 -m "Production release v1.0.0"
git push origin v1.0.0

# Deploy (prefer rolling update via your orchestrator; for single-host:)
docker compose pull
docker compose up -d --wait --wait-timeout 300

# Apply Kong config (if changed)
./scripts/kong-setup.sh

# Verify
./scripts/test.sh
```

## Post-deploy (first 60 min)
- [ ] Kong 5xx rate < 0.5%.
- [ ] p95 latency < 500 ms.
- [ ] Consumer lag per topic near 0.
- [ ] No repeated container restarts (`docker compose ps`).
- [ ] No firing critical alerts.

## Rollback

```bash
git checkout <previous-tag>
docker compose up -d --wait
./scripts/restore.sh ./backups/<pre-deploy-ts>   # only if data corruption
```

Rollback criteria (immediate):
- 5xx rate > 5% for 5 min
- Workflow failure rate > 10% for 10 min
- p95 latency > 5 s for 10 min
- Any `PostgresDown` / `RedisDown` / `RedpandaUnderReplicated` firing
- Security incident

## Scale up

- More Kong: `docker compose up -d --scale kong=3` (behind the LB).
- More n8n workers: `docker compose --profile ha up -d --scale n8n-worker=N`. Measured default is ~240 rps single-process; each worker adds parallel execution. See [`performance.md`](performance.md).
- More Kafka throughput: add a 4th/5th broker, then `rpk cluster reassign`.
- Raise Redpanda memory: edit each `redpanda-N` service's `--memory` flag.
- Load-test after any scale change: `./scripts/load-test.sh` generates a fresh report.

## Useful

```bash
# Kong
curl -s http://127.0.0.1:8001/status | jq .
curl -s http://127.0.0.1:8001/routes | jq '.data[].paths'

# Redpanda
docker exec gateway-redpanda-0 rpk cluster health --brokers redpanda-0:29092
docker exec gateway-redpanda-0 rpk topic describe samsara-events --brokers redpanda-0:29092
docker exec gateway-redpanda-0 rpk group list --brokers redpanda-0:29092

# n8n
curl -s -u $N8N_BASIC_AUTH_USER:$N8N_BASIC_AUTH_PASSWORD http://127.0.0.1:5678/healthz

# Prometheus
curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job:.labels.job, up:.health}'
```

# Security Guide

> This document describes the controls **that are now in place** in this repo.
> Everything listed in the "Controls in place" section is already configured in
> `docker-compose.yml`, `kong/kong.yml`, or the workflow files. Items in "Before
> production" are still on you.

---

## Controls in place

### 1. Admin surface isolation
- Kong admin (`8001`) and manager (`8002`) are bound to `127.0.0.1` in
  `docker-compose.yml`. They are unreachable from the host's external IP.
- Same for n8n (`5678`), Redpanda Console (`8080`), Grafana (`3002`),
  Prometheus (`9090`), Alertmanager (`9093`), Loki (`3100`).
- Only the public proxy ports `8000`/`8443` listen on `0.0.0.0`.
- Reach admin UIs from a workstation via SSH tunnel or VPC-internal LB.

### 2. Kong runs DB-less (declarative)
- `KONG_DATABASE: "off"`, `KONG_DECLARATIVE_CONFIG: /etc/kong/kong.yml`.
- No `kong-database` container, no admin API writes in prod paths.
- All routes / plugins / consumers / API keys live in `kong/kong.yml` and are
  code-reviewed. Imperative `curl` setup scripts are deprecated.

### 3. Auth, rate-limit, validation on every route
`kong/kong.yml` attaches these plugins **globally** (i.e., to every route):
- `key-auth` — `X-API-Key` required. Credentials hidden from upstream.
- `rate-limiting` — 100/min, 5000/hr, **`policy: redis`** so counters are shared
  across Kong replicas.
- `request-size-limiting` — 10 MB max.
- `cors` — allowed origins pinned (edit per env).
- `correlation-id` — every request gets an `X-Request-Id`, logged and forwarded.
- `response-transformer` — HSTS, X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy, Permissions-Policy, CSP; removes `Server`/`X-Powered-By`.
- `ip-restriction` — RFC1918 allowlist by default (edit per env).
- `prometheus` — per-route request/latency/bandwidth metrics.

Routes are HTTPS-only (`protocols: [https]`, `https_redirect_status_code: 301`).

### 4. Samsara webhook HMAC verification
The `/samsara` route runs a `pre-function` Lua plugin that:
- Requires `X-Samsara-Signature` header.
- Computes `HMAC-SHA256(body, SAMSARA_WEBHOOK_SECRET)`.
- Compares constant-time; returns `401` on mismatch.
- Requests that reach n8n have **already been authenticated**.

### 5. PII minimization in Samsara workflow
`workflows/samsara-webhook-to-kafka.json` redacts driver names and formatted
addresses before producing to Kafka. Only IDs + coordinates + speed/heading are
published.

### 6. Kafka durability
- Redpanda runs as a **3-node cluster** with `default_topic_replications=3`.
- `scripts/create-topics.sh` creates topics with `replication=3`,
  `min.insync.replicas=2`, `compression.type=zstd`, explicit retention.
- Producers in workflows use `acks=-1` (all) and `gzip` compression.
- Consumers disable auto-commit; offsets commit after downstream success.
- Dead-letter queue (`errors-dlq`) is wired to every workflow; NetSuite has
  bounded exponential-backoff retry with `max_retries=5` before DLQ.

### 7. Secrets
- `.env` is gitignored (`.gitignore`). `.env.example` holds only `CHANGE_ME`
  placeholders.
- `gitleaks` runs in CI (`.github/workflows/ci.yml`) and **blocks** on hits.
- A grep for the known Samsara token pattern blocks merges.
- Backups (`scripts/backup.sh`) are encrypted with `age` when
  `BACKUP_AGE_RECIPIENT` is set.
- n8n has `N8N_ENCRYPTION_KEY` set explicitly — this encrypts credentials at
  rest in Postgres.
- Postgres `password_encryption = scram-sha-256`.

### 8. Observability & audit
- Prometheus scrapes Kong, n8n, Redpanda (all 3 nodes), Postgres, Redis, the
  host, and cAdvisor.
- Alertmanager routes `severity=critical` to PagerDuty, warnings to Slack.
- Rules in `config/prometheus/rules/gateway.rules.yml` cover: 5xx rate, p95
  latency, consumer lag, Kafka under-replication, n8n queue backlog, disk/mem,
  container restart loops, Postgres/Redis down.
- Loki + Promtail collect all container logs with `correlation-id` extraction.
- Grafana admin signup disabled, anon disabled, secure cookies, HSTS on,
  basic CSP on.
- n8n diagnostics + version banners disabled. Execution history pruned at
  14 days (configurable).

### 9. CI security gates (blocking)
- `gitleaks` secret scan
- Trivy config scan (CRITICAL/HIGH fails)
- Trivy filesystem scan (CRITICAL/HIGH fails)
- Trivy image scans for every image (CRITICAL fails)
- YAML lint, shellcheck, JSON parse on all workflows
- Kong declarative config parsed by Kong itself
- Smoke test brings the stack up and verifies admin endpoints are healthy

---

## Before production — still on you

- [ ] **Rotate the Samsara token** if it was ever committed, shared, or stored
      in any `.env` that lived on disk in its original form.
- [ ] Move secrets from `.env` to a real secret manager (AWS SM / Vault / SSM)
      and inject them at runtime. The compose file already consumes env vars —
      wiring into your secret store is a drop-in.
- [ ] Replace the self-signed cert in `config/kong/certs/` with a real one
      (ACM / Let's Encrypt / internal CA). Consider `cert-manager` if on k8s.
- [ ] Edit `kong/kong.yml`:
  - Replace every `REPLACE_ME_*_api_key` with a rotated, strong key.
  - Tighten the `ip-restriction` allowlist to the actual caller CIDRs.
  - Pin `cors.origins` to real origins.
- [ ] Set `SAMSARA_WEBHOOK_SECRET` in `.env` and in the Samsara console.
- [ ] Replace Postgres with a managed service (RDS/CloudSQL) with TLS, PITR,
      encrypted storage.
- [ ] Move Redpanda to Redpanda Cloud / MSK, or run the 3 nodes on 3 hosts
      with dedicated volumes. Enable tiered storage for long retention.
- [ ] Put Kong behind an L4/L7 LB and run ≥ 2 Kong replicas.
- [ ] Enable SSO (OIDC/SAML) on n8n and Grafana; disable basic auth.
- [ ] Wire `ALERTMANAGER_SLACK_WEBHOOK` and `ALERTMANAGER_PAGERDUTY_KEY`.
- [ ] Run a load test against the target `5k req/s` SLO in the deployment
      checklist.
- [ ] Run a DR drill (`./scripts/backup.sh` → wipe volumes → `./scripts/restore.sh`).

---

## Threat model summary

| Threat                                    | Mitigation |
|-------------------------------------------|------------|
| Admin API exposed to the internet         | 127.0.0.1 binding + DB-less Kong |
| Unauthenticated / forged webhook ingest   | key-auth + Samsara HMAC pre-function |
| Credential leak via git                   | `.gitignore`, gitleaks CI gate, `.gitleaks.toml` |
| Credential leak via backup                | `age` encryption in `backup.sh` |
| DoS via payload size                      | `request-size-limiting` (10 MB) + nginx client_max_body_size |
| DoS via request rate                      | `rate-limiting` (Redis-shared counters) |
| MitM on public proxy                      | HTTPS-only routes + HSTS + modern TLS cipher suite |
| Consumer lag → data loss                  | `acks=all`, RF=3, `min.insync=2`, DLQ, alert on lag > 10k |
| Data loss on node failure                 | 3-broker Redpanda cluster, replication=3 |
| PII egress to downstream                  | Redaction in Samsara parser |
| Supply-chain CVEs                         | Trivy image scans in CI (fail on CRITICAL) |
| Stolen n8n credentials at rest            | `N8N_ENCRYPTION_KEY` + Postgres at-rest encryption |

---

## Resources

- [Kong production hardening](https://docs.konghq.com/gateway/latest/production/security/)
- [n8n security](https://docs.n8n.io/hosting/security/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)

# Gateway — Session State / Resume Doc

Last updated: 2026-04-17

Single source of truth for where we are. If you open this repo cold,
read this first.

---

## TL;DR

Repo at `github.com/ciscosanchez/gateway` is production-hardened but
not deployed. Two runnable profiles:

- **Lite** (laptop POC): `make demo`
- **HA** (multi-node prod): `make up`

Latest commit on `main`: `9522c91` — "Add lite POC profile, Makefile,
fix NetSuite workflow auth".

---

## What's done

All 15 hardening items + 3 POC items complete and pushed. Highlights:

- Kong DB-less declarative (`kong/kong.yml`): key-auth, redis
  rate-limit, HMAC pre-function for Samsara, CORS, ip-restriction,
  security headers.
- Admin APIs (Kong 8001/8444, Redpanda console 8080, Grafana 3002,
  Prometheus 9090, Alertmanager 9093, n8n 5678, Redis 6379,
  Postgres 5432) bound to `127.0.0.1` only.
- Redpanda 3-node KRaft in HA (RF=3, min.insync=2, zstd); 1-node
  dev mode in lite.
- n8n queue mode + workers (HA profile).
- Observability: Prometheus + alert rules, Alertmanager (Slack +
  PagerDuty receivers, env-driven), Grafana (provisioned datasources),
  Loki + Promtail (logs profile), cAdvisor, node/postgres/redis
  exporters.
- Encrypted backups via `age`; daily cron script ready.
- CI: gitleaks + Trivy config/fs + compose validate.
- Secrets: all `.env` values are `REPLACE_ME` placeholders.
- Workflows hardened: manual commit, DLQ, backoff, timeouts.
- `Makefile` + `docker-compose.lite.yml` for POC.

---

## File map

```
.env                        placeholders only — never real values
.env.example                template
Makefile                    demo / up / down / tunnel / test / topics / logs
docker-compose.yml          base + HA profile (redpanda-1/2, n8n-worker, loki, promtail)
docker-compose.lite.yml     overlay: 1 Redpanda dev mode, no Loki, no worker
kong/kong.yml               declarative: services, routes, plugins, consumers
config/prometheus/          scrape + alert rules
config/alertmanager/        Slack + PagerDuty receivers (env-driven)
config/loki/, config/promtail/, config/grafana/
scripts/
  setup.sh                  cert + compose up --wait + topics
  test.sh                   smoke test
  validate-config.sh        preflight
  create-topics.sh          RF / min.insync / compression
  kong-setup.sh             hot-reload kong.yml
  backup.sh, restore.sh     age-encrypted
  cron/gateway-crontab      daily 02:15
workflows/                  5 n8n workflows (samsara, netsuite, patterns)
docs/                       getting-started, SECURITY, DEPLOYMENT-CHECKLIST,
                            netsuite-integration, samsara-integration, kong-plugins
.github/workflows/ci.yml    gitleaks + trivy + compose validate
```

---

## Three tracks

### TRACK A — Run POC locally (10 min)

```bash
cd /Users/ciscosanchez/Code/gateway
cp .env.example .env
# Fill every REPLACE_ME in .env:
#   openssl rand -base64 48   # POSTGRES_PASSWORD, KONG_PG_PASSWORD
#   openssl rand -base64 32   # REDIS, N8N_BASIC_AUTH, GF_SECURITY_ADMIN
#   openssl rand -hex 16      # N8N_ENCRYPTION_KEY
#   openssl rand -hex 32      # SAMSARA_WEBHOOK_SECRET

make demo           # lite stack up + wait healthy
make topics
make kong-reload
make test

# separate terminal:
brew install cloudflared  # if missing
make tunnel               # prints public https URL
```

Paste the cloudflared URL into Samsara's webhook config, append
`/samsara`. Admin UIs on loopback: n8n :5678, Grafana :3002, Redpanda
Console :8080, Prometheus :9090, Alertmanager :9093.

### TRACK B — NetSuite (20 min, mostly NetSuite UI)

**In NetSuite:**

1. Setup → Company → Enable Features → SuiteCloud:
   - TOKEN-BASED AUTHENTICATION on
   - REST WEB SERVICES on
2. Setup → Integration → Manage Integrations → New
   - Name: "Gateway"
   - TBA on; uncheck TBA Authorization Flow + User Credentials
   - Save — copy Consumer Key + Secret (shown once)
3. Setup → Users/Roles → Manage Roles → New or reuse
   - Permissions: Customers View, Sales Order Create/Edit,
     Find Transaction View, Login Using Access Tokens Full,
     REST Web Services Full
4. Assign role to a User
5. Setup → Users/Roles → Access Tokens → New
   - App = integration, User+Role from above
   - Save — copy Token ID + Secret (shown once)
6. Note Account ID (e.g. `1234567` prod, `1234567_SB1` sandbox)

**In `.env`:**

```
NETSUITE_ACCOUNT_ID=1234567
NETSUITE_CONSUMER_KEY=…
NETSUITE_CONSUMER_SECRET=…
NETSUITE_TOKEN_ID=…
NETSUITE_TOKEN_SECRET=…
```

**One-time n8n credential** (n8n stores creds in its own encrypted DB):

1. http://localhost:5678 → Credentials → New → OAuth1 API
2. Fill:
   - Consumer Key / Secret from integration
   - **Signature Method: HMAC-SHA256** (critical — NetSuite rejects SHA1)
   - Realm: Account ID
   - Advanced: Token / Token Secret, Add Auth Data To: Header
3. Name `NetSuite TBA`, save
4. Import `workflows/netsuite-create-sales-order.json`, point HTTP
   node at `NetSuite TBA` credential, activate

**Smoke test:**

```bash
docker compose exec redpanda-0 rpk topic produce orders <<'EOF'
{"customer_id":"<real-id>","line_items":[{"netsuite_item_id":"<real-id>","quantity":1,"unit_price":10,"description":"POC"}],"external_order_id":"POC-001"}
EOF
```

Watch n8n Executions tab. New Sales Order should appear in NetSuite.

**Open questions for user:**
- Sandbox or prod?
- Real customer internal ID for test
- Real item internal ID for test
- SKU lookup (SuiteQL step) or raw internal ID in payload?

### TRACK C — VPS deploy (blocked on host)

The initial `appstage.goarmstrong.com` was too busy:
- Port 80 taken, Next.js apps on 3000/3001, host Postgres on 5432
- VPN-only (10.105.0.211), not internet-reachable for Samsara webhooks
- Missing tools: age, ufw/firewalld setup, fail2ban, certbot, jq

**User is picking another host.** Minimum for HA:
- 4+ vCPU, 8+ GB RAM, 80+ GB disk
- Public IP
- Ubuntu 22.04/24.04 or Rocky 9, clean (nothing on 80/443)

**When host is ready, agent runs:**
1. Discovery (uname, docker version, tools)
2. Install missing: age, firewalld/ufw, fail2ban, certbot, jq
3. Firewall: allow 22/80/443, deny else
4. Harden sshd (key-only, no root)
5. `git clone` to `/opt/gateway`
6. Generate secrets with `openssl rand`, write `.env` chmod 600
7. Real API keys for 4 Kong consumers → inject into kong.yml
8. Let's Encrypt via certbot standalone
9. age keypair → `BACKUP_AGE_RECIPIENT`
10. `make up` + `make topics` + `make kong-reload` + `make test`
11. systemd unit + `systemctl enable --now gateway`
12. Install backup crontab
13. Verify external: `curl -kI https://<host>/samsara` → 401

**User decisions needed:**
1. SSH alias of new host
2. Domain for Let's Encrypt (or OK with self-signed)
3. Slack webhook / PagerDuty key / skip?
4. Public IP direct or via reverse proxy?
5. OK to rotate leaked Samsara token (user does it in Samsara UI)?

---

## Known issues / gaps

1. **Leaked Samsara token** (prefix `samsara_api_2KX...`, full value
   redacted here; see repo history or your Samsara console) was in an
   earlier commit's `.env`. Must rotate in Samsara console.
2. **CI Trivy image scans** may flag HIGH CVEs in upstream images. If
   CI red, either pin digests, mark image-scan continue-on-error, or
   accept with dated exception.
3. **Single-host SPOF** even HA profile. Real HA = 3 hosts; deferred.
4. **No external secret manager**. `.env` + `chmod 600` for now.
5. **Grafana dashboards empty** — provision via UI or drop JSON in
   `config/grafana/dashboards/`.
6. **Alertmanager receivers null** until user pastes webhook/key.

---

## Resume instructions (for fresh agent)

1. `cd /Users/ciscosanchez/Code/gateway`
2. `git status && git log --oneline -5` — should be on `main` at or
   past `9522c91`
3. Read this file
4. Ask user which track to push:
   - demo now → Track A
   - NetSuite working → Track B (needs creds)
   - real VPS → Track C (needs host)
5. Add new todos per track picked.

---

## Commit history

```
9522c91  Add lite POC profile, Makefile, fix NetSuite workflow auth
389795c  Production hardening: secrets, Kong DB-less + HMAC, HA topology, observability
61a0d40  initial
```

Author: `Gateway DevOps <devops@gateway.internal>`.

---

## User still owes

- [ ] Rotate Samsara API token in Samsara console
- [ ] NetSuite TBA values (5 strings) + sandbox/prod + test customer/item IDs
- [ ] Pick deploy host for Track C
- [ ] Domain for TLS (or say self-signed OK)
- [ ] Slack webhook / PagerDuty key (or say skip)
- [ ] Register Samsara webhook at tunnel/domain URL
- [ ] Generate + distribute real values for 4 Kong consumer API keys

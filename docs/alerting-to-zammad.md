# Alerting to Zammad — with false-positive filtering

The gateway routes **only CRITICAL alerts** into Zammad as tickets, and
five separate filters make sure the helpdesk doesn't get noise. Warnings
and below fire on Slack only.

## Flow

```
 ┌──────────────┐  rule fires (5xx rate, consumer lag, admin rotation fail, ...)
 │  Prometheus  │────┐
 └──────────────┘    │  for: ≥5m    (filter 1: minimum firing duration)
                     ▼
              ┌────────────────┐  group_wait: 30s, group_interval: 5m
              │  Alertmanager  │  repeat_interval: 4h   (filter 2: grouping)
              └────────────────┘  severity=critical -> zammad receiver (filter 3)
                     │
                     ▼ webhook
              ┌─────────────────────────┐
              │  n8n workflow:          │
              │  alertmanager-to-zammad │
              └─────────────────────────┘
                     │                     ↓ search Zammad for external_id
                     ├── exists ─▶ append article (filter 4: fingerprint dedup)
                     └── new    ─▶ create ticket
                                          ↑
                                  status=resolved appends
                                  '[auto-resolved]' note but
                                  doesn't close  (filter 5:
                                  no auto-close)
                     │
                     ▼
              ┌──────────────┐
              │    Zammad    │   one ticket per alert fingerprint
              └──────────────┘   reopens on re-fire
```

## The five filters

| # | Filter | Where | What it stops |
|---|---|---|---|
| **1** | Minimum firing duration | `for:` in each Prometheus rule (≥5m) | Spikes that self-heal in <5 min never reach Alertmanager. |
| **2** | Grouping | `group_by: [alertname, integration, service]` in `alertmanager.yml.tpl` | A storm of 100 related alerts becomes 1 webhook delivery. |
| **3** | Severity routing | `matchers: [severity="critical"]` on the zammad receiver | Warnings and below never hit the workflow. They stay on Slack. |
| **4** | Fingerprint dedup | `sha256(groupKey)` → `external_id` in the n8n workflow | A re-firing alert appends an article instead of opening a new ticket. |
| **5** | No auto-close on resolve | Workflow appends `[auto-resolved]` note, doesn't change ticket state | A resolve-then-refire can't hide behind a wrongly-closed ticket. Operators close manually once they confirm resolution. |

## Setup

### 1. Mint a Zammad API token

In Zammad: *Profile → Token Access → Create Token*. Minimum permissions:
- `ticket.agent` (read + write tickets)

Copy the token — it's shown once. Paste it into your gateway's `.env`:

```bash
ZAMMAD_URL=https://helpdesk.goarmstrong.com
ZAMMAD_API_TOKEN=<token from Zammad>
ZAMMAD_GROUP=Users            # or whatever group you want alerts filed under
ZAMMAD_CUSTOMER=info@goarmstrong.com
```

### 2. Restart n8n so it picks up the env

```bash
docker compose up -d n8n n8n-worker
# or via admin UI:
# Credentials → Edit ZAMMAD_* → Save → Apply (restart)
```

### 3. Import the workflow into n8n

One-time: Workflows → Import from File →
`workflows/alertmanager-to-zammad.json`. Activate it.

### 4. Test end-to-end

The workflow exposes a webhook at `http://n8n:5678/webhook/alertmanager`
(internal network). To test without waiting for a real alert:

```bash
# from the host, forward a fake Alertmanager payload
docker compose exec n8n sh -c 'apk add --no-cache curl 2>/dev/null; true'
docker compose exec n8n curl -s -X POST http://localhost:5678/webhook/alertmanager \
  -H "Content-Type: application/json" \
  -d @- <<'EOF'
{
  "status": "firing",
  "groupKey": "{}:{alertname=\"TestAlert\"}",
  "commonLabels": { "alertname": "TestAlert", "severity": "critical", "service": "admin-ui" },
  "commonAnnotations": { "summary": "test from operator", "description": "manual e2e verification" },
  "alerts": [{ "status": "firing", "startsAt": "2026-04-19T12:00:00Z", "labels": {"instance":"test"}, "annotations": {"summary":"test"} }]
}
EOF
```

Expect `HTTP 200 {"accepted":true,"zammad_action":"created","dedup_key":"gw-..."}`.
Check Zammad — a new ticket should exist with the dedup key as `external_id`.

Repeat the same curl — the second call should return `"zammad_action":"appended"`
and the Zammad ticket should now have two articles instead of two tickets.

## What's wired to Zammad today

These Prometheus rules are `severity=critical` and go through the full pipeline:

| Rule | `for:` | Condition |
|---|---|---|
| `KongHighErrorRate` | 5m  | Kong 5xx rate > 5% |
| `KongDown` | 2m | Kong scrape target down |
| `RedpandaUnderReplicated` | 5m | Any under-replicated partition |
| `AdminUIErrorRate` | 10m | admin-ui 5xx > 5% |
| `AdminUIRotationFailures` | 5m | more rollbacks than successful rotates |
| `ContainerRestartingRepeatedly` | 5m | >2 restarts in 15m |
| `PostgresDown`, `RedisDown` | 2m | scrape target down |

Warnings (Kong p95 latency, Kafka lag, n8n queue backlog, n8n workflow
failures, disk, memory) go to Slack only. If a warning consistently
tracks a real problem, promote its severity to `critical` — it'll start
creating tickets.

## When Zammad is the wrong destination

- **Page-worthy incidents** — these should also hit PagerDuty. Alertmanager's
  `continue: true` on the zammad receiver means both fire; PagerDuty does the
  phone call, Zammad does the tracking.
- **Partner-facing issues** — separate webhook with different group/customer
  if you want distinct ticket pools for internal vs. customer-impacting.
- **Information-only alerts** (deploy done, backup completed) — these shouldn't
  have `severity: critical`. Keep them on Slack.

## Adjusting the filters

- **Too many tickets for a specific alert** → raise its `for:` clause in
  `config/prometheus/rules/gateway.rules.yml` to 15m / 30m. Or demote severity
  to `warning`.
- **Missing alerts** → shorten `for:` (but remember filter 1 is the most
  effective anti-flap mechanism we have).
- **Tickets landing in the wrong group** → change `ZAMMAD_GROUP` in `.env`
  and restart n8n.
- **Duplicate tickets** despite dedup → the `groupKey` changes when label
  values change. Check `group_by` in `alertmanager.yml.tpl` — narrower grouping
  means more unique fingerprints, more tickets.

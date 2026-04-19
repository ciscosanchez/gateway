# Gateway Admin UI

A profile-gated, loopback-only FastAPI service that unifies credential
management across the three places the gateway actually stores secrets:

| Source    | Read | Write | How it's stored                                     |
|-----------|------|-------|-----------------------------------------------------|
| **env**   | ✅   | ✅    | Atomic rewrite of `/.env` + restart trigger         |
| **n8n**   | ✅   | ✅    | n8n's REST API (basic auth) or `/api/v1` (API key)  |
| **Kong**  | ✅   | ✅    | Edit `kong/kong.yml` + POST `/config` (hot-reload)  |

Plus an append-only SQLite audit log, per-integration health probes, a
rotate-with-healthcheck+rollback flow for env vars, and a docker-socket-
backed Apply button that restarts the services affected by any pending
env changes.

---

## Run it

```bash
# From the repo root, after .env is filled in
docker compose --profile admin up -d admin-ui

# Then
open http://127.0.0.1:7070
```

The `admin` profile gates this container so it's opt-in: the default and
lite compose stacks skip it. Only `127.0.0.1:7070` is published.

### Required env (in `.env`)

- `ADMIN_UI_USER`, `ADMIN_UI_PASSWORD` — HTTP Basic credentials. When
  both are set, every request to `/api/*` and `/` is gated. When either
  is unset, the gate is disabled (wireframe/dev mode only).
- `N8N_BASIC_AUTH_USER`, `N8N_BASIC_AUTH_PASSWORD` — used to read n8n
  credentials via the internal REST API.
- `N8N_API_KEY` *(optional)* — if set, the admin-ui talks to n8n's
  stable `/api/v1` instead of the internal `/rest`. Mint this in n8n's
  UI under *Settings → API*.

Everything else is wired by compose.

---

## Features

### Credentials screen

- **All** / **env vars** / **n8n** / **Kong consumers** tabs.
- Each row has **Edit**, **Delete**, and — for env vars only — **Rotate**.
- Modal's source picker dispatches to the right backend endpoint.
  Selecting *n8n cred* swaps the single-value field for a type-specific
  form (httpHeaderAuth, httpBasicAuth, oAuth1Api/NetSuite TBA).
- Values are masked everywhere. Plaintext never round-trips on read.

### Rotate-with-healthcheck

`POST /api/credentials/env/{name}/rotate` atomically writes the new
value, runs the integration's health probe against the fresh `.env`, and
— if the probe fails — restores the old value. Known probes:

| Integration | Probe                                                      |
|-------------|------------------------------------------------------------|
| Samsara     | `GET https://api.samsara.com/fleet/vehicles` with Bearer   |
| Unigroup    | `POST` Keycloak `/token` with `client_credentials`         |
| NetSuite    | placeholder (OAuth1 TBA sign-probe deferred)              |

Integrations without a probe are trivially verified — the write applies
and a restart is scheduled.

### Pending-restart banner

Every env var in `sources/env.py` REGISTRY carries a `services` list
of compose services that consume it. When a write lands, those services
are marked pending in SQLite. The UI shows a blue banner:

> 2 changes pending. Apply to restart: `n8n` `n8n-worker` · **Apply**

The Apply button calls `POST /api/services/restart` which, via the
mounted `/var/run/docker.sock`, looks up containers by their
`com.docker.compose.service` label and restarts them. Successfully
restarted services clear their pending flag; failures leave the flag set
so the banner keeps prompting.

### Audit log

SQLite table `audit_events`, append-only. Each row:
- `ts`, `actor` (from HTTP Basic), `action` (`create` / `update` /
  `delete` / `rotate` / `rotate_failed` / `rollback_failed` / `restart`
  / `test`), `source` (`env` / `n8n` / `kong` / `docker` /
  `integration`), `name`, `integration`, `client_ip`, `note`
- `before_hash`, `after_hash` — **sha256** of old/new values. Never
  plaintext. This is what lets the log compare "is the live value
  still the one we wrote last?" without ever storing secrets.

---

## API

All endpoints are under HTTP Basic auth when `ADMIN_UI_USER` /
`ADMIN_UI_PASSWORD` are set.

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/api/health` | source reachability, writable flags, docker socket state, `n8n_types` schema |
| GET    | `/api/credentials` | all sources, filter by `?source=env\|n8n\|kong` or `?integration=...` |
| GET    | `/api/credentials/{name}` | one row by name |
| PUT    | `/api/credentials/env/{name}` | upsert env var |
| POST   | `/api/credentials/env/{name}/rotate` | rotate-with-healthcheck + auto-rollback |
| DELETE | `/api/credentials/env/{name}` | blank the value |
| PUT    | `/api/credentials/n8n/{name}` | upsert n8n credential (body: `{type, data, note, existing_id?}`) |
| DELETE | `/api/credentials/n8n/{name}` | delete by resolved n8n id |
| PUT    | `/api/credentials/kong/{consumer}` | upsert a consumer's key-auth value (edits kong.yml + hot-reload) |
| DELETE | `/api/credentials/kong/{consumer}` | remove the consumer's key |
| GET    | `/api/services/pending` | list services with pending env changes |
| POST   | `/api/services/restart` | restart the named services via docker socket |
| GET    | `/api/integrations` | probe names |
| POST   | `/api/integrations/{name}/test` | run the probe |
| GET    | `/api/audit?limit=N&name=X` | recent audit rows |

Full OpenAPI at `/docs` (FastAPI).

---

## Safety rails

| Rail | How it's enforced |
|------|-------------------|
| Plaintext values never echoed | Responses return `value_masked` only; audit stores sha256 hashes |
| Writes are atomic | Temp file + `os.replace()` for `.env`; Kong's `/config` validates before we touch `kong.yml` on disk |
| Unknown-var / unknown-consumer / unknown-n8n-type rejected | `REGISTRY` in `sources/env.py`, kong.yml consumer list, `WRITABLE_TYPES` in `sources/n8n_api.py` |
| Rotate rolls back on probe failure | `rotate_failed` audit row, old value restored, HTTP 400 with probe detail |
| Loopback-only by default | Compose publishes `127.0.0.1:7070` only |
| Profile-gated | Only starts with `--profile admin`; base + lite stacks unaffected |
| Auth-gated when creds set | HTTP Basic on every route; `secrets.compare_digest` to dodge timing oracles |

---

## Known privilege trade-offs

The container runs as **root** and mounts `/var/run/docker.sock`. Both
are intentional:

- Root: needed to write through the bind-mounted `.env` regardless of
  host file ownership, and to speak to the docker socket.
- Docker socket: powers the restart trigger. **This is effectively root
  on the host.**

Trivy DS-0002 (no USER in Dockerfile) is accepted in `.trivyignore`
with rationale. If we ever expose the admin UI beyond loopback, split
these privileges — a non-root web process talking to a privileged
helper over a narrow IPC.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                  admin-ui container (profile: admin)            │
│                                                                 │
│  FastAPI ──── sources/env.py     ─► /app/env/.env (rw bind)    │
│           └── sources/n8n_api.py ─► http://n8n:5678 (REST API) │
│           └── sources/kong_api.py─► http://kong:8001 (admin) + │
│                                     /app/kong/kong.yml (rw)    │
│           └── services.py        ─► /var/run/docker.sock       │
│           └── healthchecks.py    ─► Samsara / Unigroup / ...   │
│           └── audit.py           ─► /app/data/audit.db (SQLite)│
│                                                                 │
│  Static HTML served at /  (same origin as API)                  │
└────────────────────────────────────────────────────────────────┘
```

---

## Source layout

```
admin-ui/
├── index.html              # static SPA, flips to "Live" when backend reachable
├── README.md               # this file
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py              # FastAPI routes + auth gate
    ├── audit.py            # SQLite audit + pending_restarts
    ├── services.py         # docker-socket restart helper
    ├── healthchecks.py     # per-integration probes
    └── sources/
        ├── env.py          # REGISTRY + read/write .env
        ├── n8n_api.py      # n8n credentials
        └── kong_api.py     # Kong consumers + key-auth via kong.yml
```

---

## What's deferred

- **OIDC in front** (oauth2-proxy sidecar) — HTTP Basic is the MVP.
- **Per-role gating** (read vs rotate vs restart permissions).
- **Expiry reminders** — env schema has no `expires_at` yet.
- **NetSuite OAuth1 TBA sign-probe** — today we only verify that
  `NETSUITE_ACCOUNT_ID` is set. A real probe would sign a GET against
  SuiteTalk with the five fields.

These are all tractable follow-ups; none are prerequisites for running
the current flow.

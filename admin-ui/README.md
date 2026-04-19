# Gateway Admin — Wireframe

Static HTML mockup of the single admin panel we'd build to replace the
scattered "edit `.env` + restart" / n8n Credentials UI / Kong Manager
dance. **Nothing here is wired up.** Click around to evaluate the shape
and flow, then tell me what to cut / expand / rethink before we spend
time on a real build.

## View it

```bash
open admin-ui/index.html           # macOS
xdg-open admin-ui/index.html       # Linux
```

Or serve it locally if your browser gets grumpy about `file://`:

```bash
python3 -m http.server -d admin-ui 8765
# then http://localhost:8765
```

## Screens included

| Route                  | What it covers                                                        |
|------------------------|------------------------------------------------------------------------|
| `#dashboard` (default) | 4 stat cards + grid of integrations with status dots.                  |
| `#credentials`         | Unified credential table with tabs for **env vars / n8n / Kong**. Each row has edit / rotate / delete actions and a status badge. |
| `#integrations/<key>`  | Per-integration detail: summary, linked credentials, recent activity. |
| `#audit`               | Immutable history: who rotated what, when.                             |
| **Add credential modal** | Source picker (env / n8n / kong) + name + integration + value + optional expiry. |

## What's mocked

- The 13 credentials listed are hardcoded in `index.html`.
- Status dots are manual colors, not reading from real health checks.
- "Save" / "Delete" / "Rotate" buttons pop an alert instead of calling an API.
- Audit log is three fake rows.

## What a real build would actually need

### Backend (the hard part)

A small service (FastAPI / Go) that gates everything behind an admin role
and speaks three very different APIs:

| Source | How to read | How to write |
|---|---|---|
| **Env vars** in `.env` | Read the file, parse, show masked values. Never echo the full value back to the client once saved. | Edit `.env` safely (lock file + atomic rename) and call `docker compose up -d n8n n8n-worker` (or equivalent for the services that consume the var). Log before/after in audit. |
| **n8n credentials** | n8n's [public API](https://docs.n8n.io/api/) — `GET /rest/credentials` — with an admin API key. | `POST/PATCH/DELETE /rest/credentials`. n8n handles hot-reload. |
| **Kong consumers** | Kong admin API (loopback) — `GET /consumers` and `GET /consumers/<name>/key-auth`. | `POST/PATCH/DELETE`. Kong applies instantly without restart. |

The backend also owns the audit log (append-only, Postgres table) and the
expiry-reminder cron. Everything writes through the backend, never
directly to the three source systems — otherwise the audit log lies.

### Auth

- Behind Kong's same key-auth, but with a dedicated `admin-ui` consumer
  pinned to an IP allowlist + a stronger rate limit.
- Or: OIDC in front (Keycloak / Auth0) so multiple humans can be named
  in the audit log instead of a shared "admin".

### Safety rails (non-negotiable for prod)

1. **Never display the plaintext secret after save.** First save echoes
   once with a "copy now — you won't see this again" banner.
2. **Deletes are two-step** with a confirmation that shows dependent
   workflows / routes.
3. **Rotate ≠ delete-then-add.** Rotate writes the new value, waits
   for green health, then retires the old one.
4. **All writes are audit events** (actor, timestamp, before/after diff
   with values redacted, source IP).

### Rough scope to a working MVP

- Read-only view across all three sources: ~1 day.
- Full CRUD + audit: ~1 week.
- Rotate-with-health-check + expiry reminders: ~1 more week.

Call it **2 weeks for a production-quality build.** Wireframe is ~400
lines of HTML/JS; the real value is the backend + safety rails above.

## Open questions for you

Before we turn this into real code:

- [ ] Who should be able to reach it? IP-restricted loopback, or OIDC with
      multiple named admins?
- [ ] Any credentials that should be **write-only** (no read, not even masked)?
- [ ] Do you want the rotate flow to wait for a healthcheck before
      retiring the old value, or fire-and-forget?
- [ ] Single-tenant (one admin, one gateway) or multi-environment
      (switch between staging/prod from the same UI)?

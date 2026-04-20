# Unigroup Converge Integration

## What Converge actually is

Unigroup's `learn.converge.unigroup.com` is OAuth 2.0 (Keycloak) plus a single
GraphQL endpoint and a REST documents service. **Not EDI.** The bundle on the
public docs site references:

| Purpose    | Production                                           | Staging                                                       |
|------------|------------------------------------------------------|---------------------------------------------------------------|
| Token      | `https://auth.cloud.unigroup.com/auth/realms/unigroup/protocol/openid-connect/token` | `https://auth.cloud1.unigroup.com/auth/realms/stgunigroup/protocol/openid-connect/token` |
| GraphQL    | `https://api.unigroup.com/graphql`                   | `https://api.cloud1.unigroup.com/graphql`                     |
| Documents  | `https://api.unigroup.com/documents`                 | `https://api.cloud1.unigroup.com/documents`                   |

Document endpoints seen in the wild: `GET /documents/{docId}/content`,
`POST /documents/upload/to-order/{orderId}`.

Entities in the schema (inferred from the bundle): Shipment, Quote, Booking,
Invoice, Dispatch, Tracking, Tender.

## What you need from Unigroup

Request from your Converge partner contact:

1. **A Keycloak client** in the `stgunigroup` realm first, then `unigroup` for prod.
2. **`client_id` + `client_secret`.**
3. **Required scope(s)** — the token body has a `scope` field; the realm may
   require a specific value or may accept empty.
4. **The GraphQL schema** or sample queries. The public docs don't include it.
5. **Whether Converge pushes events.** If yes, we expose `/unigroup` through
   Kong and they POST to us. If no, we poll on a cron — see below.

## Environment variables

Set in `.env` (see `.env.example`):

```bash
UNIGROUP_ENV=staging              # or production
UNIGROUP_CLIENT_ID=...            # from Unigroup
UNIGROUP_CLIENT_SECRET=...        # from Unigroup
UNIGROUP_OAUTH_SCOPE=             # leave empty unless Unigroup tells you otherwise
```

The four URL pairs (token/graphql/docs × staging/prod) have safe defaults;
only override if Unigroup gives you a region-specific host.

## Outbound flow (us → Unigroup)

Any n8n workflow that wants to hit Converge publishes a message to the
`unigroup-out` Kafka topic. The `workflows/unigroup-outbound.json` workflow
consumes it, handles the token dance, and publishes the result to
`unigroup-in`.

### Message shape

```json
{
  "operation": "graphql_query",
  "query": "query GetShipment($id: ID!) { shipment(id: $id) { id status } }",
  "variables": { "id": "SHP12345" },
  "correlation_id": "a5f2c8...",
  "_retry_count": 0
}
```

Supported `operation` values:

| operation          | Purpose                        | Required fields                                       |
|--------------------|--------------------------------|-------------------------------------------------------|
| `graphql_query`    | Read from Converge             | `query` (+ optional `variables`)                      |
| `graphql_mutation` | Write to Converge              | `query` (+ optional `variables`)                      |
| `upload_document`  | Attach a file to an order      | `document.{order_id, filename, content_base64, content_type}` |

### Result shape (on `unigroup-in`)

```json
{
  "success": true,
  "workflow": "unigroup-outbound",
  "operation": "graphql_query",
  "correlation_id": "a5f2c8...",
  "response": { "data": { "shipment": { "id": "SHP12345", "status": "IN_TRANSIT" } } },
  "env": "staging",
  "timestamp": "2026-04-18T21:42:00Z"
}
```

### Failure handling

- HTTP `401` / `429` / `5xx` → treated as transient. The message is re-queued
  on `unigroup-out` with exponential backoff (1s → 2s → 4s … capped at 60s
  plus jitter), up to 5 retries. After that, the envelope is written to
  `errors-dlq`.
- HTTP `200` with a non-empty GraphQL `errors` array is a failure at the API
  level — same retry / DLQ path.
- Any other 4xx (auth config wrong, bad query) → straight to `errors-dlq`.

## Inbound flow (Unigroup → us)

**Open question:** we don't yet know if Converge supports webhooks or GraphQL
subscriptions. Until confirmed, treat one of these as the plan:

**Option A — webhook push (preferred if supported):** Unigroup POSTs events to
`https://<our-gateway>/unigroup`. Kong already routes that path to the n8n
webhook `/unigroup`; we add a workflow that validates the payload, translates
to an internal event shape, and publishes to `orders`, `samsara-events`, or a
new `unigroup-events` topic depending on the event type.

**Option B — polling:** a cron-triggered n8n workflow runs every N minutes,
publishes a `graphql_query` message to `unigroup-out` (so it flows through the
same auth/retry plumbing), and processes the result from `unigroup-in`.
Cheaper to build, higher latency, still safe.

## Example callers

### From another n8n workflow — get a quote

```javascript
// n8n Function node
const msg = {
  operation: 'graphql_query',
  query: `query GetQuote($origin: String!, $dest: String!) {
    quote(origin: $origin, destination: $dest) { id price eta }
  }`,
  variables: { origin: $json.origin_zip, destination: $json.dest_zip },
  correlation_id: $execution.id
};
return { json: { topic: 'unigroup-out', value: JSON.stringify(msg) } };
```

Pipe that into a Kafka `sendMessage` node targeting `unigroup-out`.

### Upload a BOL PDF

```javascript
const msg = {
  operation: 'upload_document',
  document: {
    order_id: 'ORD12345',
    filename: 'bol.pdf',
    content_base64: $binary.data.toString('base64'),
    content_type: 'application/pdf'
  },
  correlation_id: $execution.id
};
return { json: { topic: 'unigroup-out', value: JSON.stringify(msg) } };
```

## Known unknowns

These need confirmation from Unigroup before going live:

- [ ] Exact scope values expected by their Keycloak realm.
- [ ] GraphQL schema (or permission to run `__schema` introspection).
- [ ] Document upload content type — is `application/json` with base64
      field correct, or do they want `multipart/form-data`?
- [ ] Webhook / event push support (Option A vs. Option B above).
- [ ] Production rate limits (staging has been generous in partner testing).

## Operations

- **Hot-reload** the workflow: re-run `./scripts/n8n-bootstrap.sh`
  (idempotent — it PATCHes existing workflows in place, properly registers
  webhooks, and handles credential ID injection). Manual fallback:
  Workflows → Import from File → `workflows/unigroup-outbound.json`, flip
  the Active toggle.
- **Dry-run** with creds: set `UNIGROUP_CLIENT_ID` / `_SECRET`, produce a
  message to `unigroup-out` with a simple query (`{ __schema { queryType { name } } }`).
  Watch `unigroup-in`.
- **Forensics** on failures: every failure lands in `errors-dlq` with the
  original message + retry count + status code.

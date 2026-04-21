# Tai TMS Integration

## Overview

Tai TMS sends financial and entity events to this gateway as HTTP webhooks. We receive them, validate, route to Redpanda, and send sync confirmations back to Tai's REST API.

**Direction**: Bidirectional
- **Inbound**: Tai → Kong → n8n (`Tai Webhook to Kafka`) → Redpanda topics
- **Outbound**: Redpanda `tai-out` → n8n (`Tai - Outbound`) → Tai REST API

**Contact**: Kyle Wang, Tai Software — `professionalservices@tai-software.com`
**Beta API**: Resets on the 1st of every month (rotate `TAI_API_KEY` on that date).

---

## 5-Minute Activation Checklist (If You Already Have Keys)

1. Add to `.env`:
   ```bash
   TAI_API_URL=https://armstrongtransportation.taibeta.net/PublicApi
   TAI_API_KEY=<tai-issued-outbound-key>
   TAI_INBOUND_API_KEY=<key-you-generate-for-tai-to-use>
   ```
2. Set the inbound key in `kong/kong.yml` under the `tai-client` consumer:
   ```yaml
   - username: tai-client
     keyauth_credentials:
       - key: <same-value-as-TAI_INBOUND_API_KEY>
   ```
3. Apply: `./scripts/kong-setup.sh`
4. Run bootstrap to import/activate workflows: `./scripts/n8n-bootstrap.sh`
5. Send Tai the webhook URLs (see [Configuring Tai](#configuring-tai-webhook-urls) below).

---

## Credentials

| Variable | Description |
|---|---|
| `TAI_API_URL` | Tai Public API base URL. Beta: `https://armstrongtransportation.taibeta.net/PublicApi`. Prod: `https://www.taicloud.net/PublicApi` |
| `TAI_API_KEY` | Outbound key — Tai issues this. Used in `Authorization: ApiKey <key>` for REST calls we make to Tai. |
| `TAI_INBOUND_API_KEY` | Inbound key — we generate this. Tai must send it as `X-API-Key` in every webhook POST. Must match the `tai-client` consumer entry in `kong/kong.yml`. |

Generate a strong inbound key: `openssl rand -hex 20`

---

## Inbound: Tai → Gateway

### What Tai sends

Tai POSTs all webhook types to a single endpoint with a `?webhook_type=<type>` query parameter. There is one URL per event type, all authenticated by the same `X-API-Key`.

### Configuring Tai Webhook URLs

In the Tai admin (Settings → Webhooks), set each URL and the shared API key header:

**Header** (all URLs): `X-API-Key: <TAI_INBOUND_API_KEY>`

| Tai Field | Gateway URL |
|---|---|
| `BillCreateUrl` | `https://<gateway>/tai?webhook_type=bill_create` |
| `InvoiceCreateUrl` | `https://<gateway>/tai?webhook_type=invoice_create` |
| `InvoiceCreateWithShipmentUrl` | `https://<gateway>/tai?webhook_type=invoice_create_with_shipment` |
| `CustomerCreateUrl` | `https://<gateway>/tai?webhook_type=customer_create` |
| `CustomerUpdateUrl` | `https://<gateway>/tai?webhook_type=customer_update` |
| `LSPCarrierCreateUrl` | `https://<gateway>/tai?webhook_type=carrier_create` |
| `LSPCarrierUpdateUrl` | `https://<gateway>/tai?webhook_type=carrier_update` |

Tai also supports shipment events (not listed in the email but handled by the workflow):

| Tai Field | Gateway URL |
|---|---|
| `ShipmentCreateUrl` | `https://<gateway>/tai?webhook_type=shipment_create` |
| `ShipmentDetailUpdateUrl` | `https://<gateway>/tai?webhook_type=shipment_detail_update` |
| `ShipmentStatusUpdateUrl` | `https://<gateway>/tai?webhook_type=shipment_status_update` |
| `ShipmentLocationUpdateUrl` | `https://<gateway>/tai?webhook_type=shipment_location_update` |
| `ShipmentLocationUpdateExtendedUrl` | `https://<gateway>/tai?webhook_type=shipment_location_update_extended` |

### Topic Routing

| `webhook_type` values | Redpanda topic |
|---|---|
| `bill_create`, `commission_bill_create` | `tai-bills` |
| `invoice_create`, `invoice_create_with_shipment` | `tai-invoices` |
| `shipment_create`, `shipment_detail_update`, `shipment_status_update`, `shipment_location_update`, `shipment_location_update_extended` | `tai-shipments` |
| `customer_create`, `customer_update` | `tai-customers` |
| `carrier_create`, `carrier_update` | `tai-carriers` |

### Kafka message envelope

Every message published to Redpanda:

```json
{
  "source":         "tai",
  "webhook_type":   "bill_create",
  "payload":        { ...Tai's original POST body... },
  "received_at":    "2026-04-21T21:32:13.065Z",
  "correlation_id": "98ee6d87-308a-436e-ad79-95c5050e5382",
  "_key":           "tai:bill_create:1776807133065",
  "_topic":         "tai-bills"
}
```

`correlation_id` is `X-Request-Id` forwarded by Kong's `correlation-id` plugin (used as the log trace key). `_key` is used as the Kafka message key for partition assignment.

### HTTP response

Success:
```json
HTTP 202
{
  "accepted": true,
  "webhook_type": "bill_create",
  "topic": "tai-bills",
  "correlation_id": "98ee6d87-..."
}
```

Auth failure (missing or wrong `X-API-Key`): Kong returns `401` before n8n is called.

---

## Outbound: Gateway → Tai REST API

### Triggering an outbound operation

Publish a message to the `tai-out` Redpanda topic:

```json
{
  "operation":      "confirm_bill_sync",
  "payload":        { "BillId": 12345, "SyncStatus": "Synced" },
  "correlation_id": "optional-trace-id",
  "_retry_count":   0
}
```

### Supported operations

| `operation` | Tai endpoint | Method |
|---|---|---|
| `confirm_bill_sync` | `/Accounting/v2/Bills/Sync` | `PUT` |
| `confirm_invoice_sync` | `/Accounting/v2/Invoices/Sync` | `PUT` |
| `post_invoice_payment` | `/Accounting/v2/InvoicePayments` | `POST` |
| `post_bill_payment` | `/Accounting/v2/BillPayments` | `POST` |

Verify exact paths against Tai's Swagger (`<TAI_API_URL>/swagger`) before using payment posting endpoints — these were not confirmed in beta.

### Retry behavior

- Transient failures (HTTP 429, 5xx): exponential backoff up to 5 retries
  - Delay: `min(60s, 1s × 2^retry_count) + 0–500ms jitter`
  - Retried by re-publishing to `tai-out` with `_retry_count` incremented
- Client errors (4xx except 429): sent directly to `errors-dlq`, no retry
- On exhaustion (retry_count ≥ 5): sent to `errors-dlq`

### Confirmation

On success the workflow publishes to `tai-updates`:
```json
{
  "success": true,
  "workflow": "tai-outbound",
  "operation": "confirm_bill_sync",
  "correlation_id": "...",
  "response": { ...Tai API response body... },
  "timestamp": "..."
}
```

---

## Kafka Topics

| Topic | Partitions | Retention | Purpose |
|---|---|---|---|
| `tai-bills` | 6 | 30d | Inbound bill events |
| `tai-invoices` | 6 | 30d | Inbound invoice events |
| `tai-shipments` | 12 | 7d | Inbound shipment events (high volume) |
| `tai-customers` | 3 | 30d | Customer entity changes |
| `tai-carriers` | 3 | 30d | Carrier entity changes |
| `tai-out` | 6 | 30d | Outbound queue → Tai REST API |
| `tai-updates` | 6 | 7d | Tai API call confirmations |

---

## Workflow Files

| File | n8n Name | Direction |
|---|---|---|
| `workflows/tai-webhook-to-kafka.json` | Tai Webhook to Kafka | Inbound |
| `workflows/tai-outbound.json` | Tai - Outbound (sync status + payments) | Outbound |

Both workflows are imported and activated by `./scripts/n8n-bootstrap.sh`.

The inbound webhook workflow must be **active** to register its webhook URL in n8n's `webhook_entity` table. If it's imported but not showing `active: true` after bootstrap, run:

```bash
# Get the workflow ID
curl -s -u $N8N_BASIC_AUTH_USER:$N8N_BASIC_AUTH_PASSWORD \
  http://localhost:5678/rest/workflows | jq '.data[] | select(.name=="Tai Webhook to Kafka") | .id'

# Activate it
curl -s -u $N8N_BASIC_AUTH_USER:$N8N_BASIC_AUTH_PASSWORD \
  -X PATCH http://localhost:5678/rest/workflows/<ID> \
  -H "Content-Type: application/json" \
  -d '{"active": true}'
```

---

## Smoke Test

```bash
# Inbound — all 7 confirmed webhook types
for wtype in bill_create invoice_create invoice_create_with_shipment \
             customer_create customer_update carrier_create carrier_update; do
  curl -sk -X POST "https://localhost:8443/tai?webhook_type=${wtype}" \
    -H "X-API-Key: ${TAI_INBOUND_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"test":true}' | jq -c '{wtype: .webhook_type, topic: .topic, http: .accepted}'
done

# Verify messages landed in Redpanda
docker exec gateway-redpanda-0 rpk topic describe tai-bills \
  -p --brokers redpanda-0:29092
# HIGH-WATERMARK should be > 0 across partitions

# Auth check — should 401
curl -sk -o /dev/null -w "%{http_code}" \
  -X POST "https://localhost:8443/tai?webhook_type=bill_create" \
  -H "Content-Type: application/json" -d '{}'
```

---

## Key Rotation

**Monthly (beta env resets the 1st of each month):**
1. Email `professionalservices@tai-software.com` or log into beta and regenerate.
2. Update `TAI_API_KEY` in `.env`.
3. `docker compose up -d n8n` (picks up new env, no restart of Kong needed).

**Inbound key rotation:**
1. Generate new key: `openssl rand -hex 20`
2. Update `TAI_INBOUND_API_KEY` in `.env`.
3. Update `kong/kong.yml` `tai-client.keyauth_credentials[0].key`.
4. `./scripts/kong-setup.sh`
5. Send new key to Tai (Kyle Wang) and confirm they've updated their webhook config before removing the old one.

---

## Tai API Reference

- **Swagger**: `https://armstrongtransportation.taibeta.net/PublicApi/swagger`
- **Webhook docs**: `https://learn.tai-software.com/knowledge/webhook-integration-setup`
- **Webhook payload examples**: `https://docs.taicloud.net/reference/publicapiwebhooksdemo_getbillcreate`

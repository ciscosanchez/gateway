# NetSuite Integration Guide

## Overview

NetSuite is the most complex integration in this stack. This guide covers the setup and best practices.

## 5-Minute Activation Checklist (If You Already Have Keys)

Use this when you already have all 5 values:

- `NETSUITE_ACCOUNT_ID`
- `NETSUITE_CONSUMER_KEY`
- `NETSUITE_CONSUMER_SECRET`
- `NETSUITE_TOKEN_ID`
- `NETSUITE_TOKEN_SECRET`

### 1) Add credentials to `.env` (1 minute)

```bash
NETSUITE_ACCOUNT_ID=1234567
NETSUITE_CONSUMER_KEY=...
NETSUITE_CONSUMER_SECRET=...
NETSUITE_TOKEN_ID=...
NETSUITE_TOKEN_SECRET=...
```

### 2) Restart n8n services so env vars are loaded (1 minute)

```bash
docker compose up -d n8n n8n-worker
```

### 3) Create n8n OAuth1 credential `NetSuite TBA` (1 minute)

In n8n UI (`http://localhost:5678`):

1. Credentials → New → OAuth1 API
2. Fill Consumer Key/Secret + Token ID/Secret
3. Set Realm = `NETSUITE_ACCOUNT_ID`
4. Set Signature Method = `HMAC-SHA256`
5. Add Auth Data To = Header
6. Save as `NetSuite TBA`

### 4) Import and activate workflow (1 minute)

1. Import `workflows/netsuite-create-sales-order.json`
2. Open the NetSuite HTTP Request node
3. Select credential `NetSuite TBA`
4. Activate the workflow

### 5) Run smoke test (1 minute)

```bash
docker compose exec redpanda-0 rpk topic produce orders <<'EOF'
{"customer_id":"<real-id>","line_items":[{"netsuite_item_id":"<real-id>","quantity":1,"unit_price":10,"description":"POC"}],"external_order_id":"POC-001"}
EOF
```

Expected result:

- n8n execution succeeds
- A new Sales Order appears in NetSuite

If it fails fast, check first:

- Account ID format (`1234567` vs `1234567_SB1`)
- Credential signature method (`HMAC-SHA256`)
- Role permissions (`REST Web Services`, `Login Using Access Tokens`, `Sales Order`)

## Phase 1: NetSuite Setup (Do This First)

### 1. Enable Token-Based Authentication (TBA)

1. **Enable Token-Based Authentication feature**:
   - Setup → Company → Enable Features → SuiteCloud tab
   - Check "Token-Based Authentication"
   - Save

2. **Create Integration Record**:
   - Setup → Integration → Manage Integrations → New
   - Name: "Gateway Integration"
   - State: Enabled
   - Token-Based Authentication: Checked
   - TBA: Authorization Flow: Checked
   - Save and copy:
     - Consumer Key
     - Consumer Secret

3. **Create Access Token**:
   - Setup → Users/Roles → Access Tokens → New
   - Application Name: Select your integration
   - User: Select service account user
   - Role: Select appropriate role
   - Token Name: "Gateway Token"
   - Save and copy:
     - Token ID
     - Token Secret

4. **Store Credentials Securely**:
   ```bash
   # Add to .env file (never commit to git!)
   NETSUITE_ACCOUNT_ID=1234567
   NETSUITE_CONSUMER_KEY=abc123...
   NETSUITE_CONSUMER_SECRET=xyz789...
   NETSUITE_TOKEN_ID=token123...
   NETSUITE_TOKEN_SECRET=secret456...
   ```

### 2. Set Up Service Account

Create a dedicated service account with minimal permissions:

1. Setup → Users/Roles → Manage Users → New
2. Name: "Gateway Service Account"
3. Email: gateway@yourcompany.com
4. Role: Create custom role with only needed permissions
5. Access: Locked to API only (no UI access)

### 3. Configure Rate Limits

Know your limits (varies by NetSuite license):
- **Concurrent requests**: Usually 5-10
- **Rate limit**: ~1000 requests per hour (varies)
- **SuiteQL limit**: 10 concurrent queries

## Phase 2: n8n Workflow Setup

### Option A: Using n8n HTTP Request Node (Recommended for Flexibility)

Create a workflow in n8n:

1. **Webhook Trigger** (receives from Kong/Kafka)

2. **HTTP Request Node** (NetSuite API call):
   ```json
   {
     "method": "GET",
     "url": "https://{{$env.NETSUITE_ACCOUNT_ID}}.suitetalk.api.netsuite.com/services/rest/record/v1/salesOrder/{{$json.orderId}}",
     "authentication": "oAuth1",
     "oAuth1": {
       "consumerKey": "={{$env.NETSUITE_CONSUMER_KEY}}",
       "consumerSecret": "={{$env.NETSUITE_CONSUMER_SECRET}}",
       "tokenKey": "={{$env.NETSUITE_TOKEN_ID}}",
       "tokenSecret": "={{$env.NETSUITE_TOKEN_SECRET}}",
       "signatureMethod": "HMAC-SHA256",
       "realm": "={{$env.NETSUITE_ACCOUNT_ID}}"
     }
   }
   ```

3. **Error Handler** (catch rate limits, retry)

4. **Transform Response**

### Option B: Using RESTlets (For Custom Logic)

When standard API doesn't cover your needs:

1. **Create RESTlet in NetSuite**:
   - File → SuiteScripts → New
   - Type: RESTlet
   - Implement GET/POST/PUT/DELETE handlers

2. **Deploy RESTlet**:
   - Customization → Scripting → Script Deployments
   - Note the URL

3. **Call from n8n**:
   ```
   https://{{accountId}}.restlets.api.netsuite.com/app/site/hosting/restlet.nl?script={{scriptId}}&deploy={{deployId}}
   ```

## Phase 3: Common Patterns

### Pattern 1: Create Sales Order

```javascript
// n8n Function Node - Prepare NetSuite payload
const order = {
  entity: { id: $json.customerId },
  tranDate: new Date().toISOString().split('T')[0],
  items: $json.lineItems.map(item => ({
    item: { id: item.itemId },
    quantity: item.quantity,
    rate: item.price
  }))
};

return { json: order };
```

Then POST to:
```
https://{{accountId}}.suitetalk.api.netsuite.com/services/rest/record/v1/salesOrder
```

### Pattern 2: Update Inventory (via SuiteQL)

```sql
-- Query current inventory
SELECT 
  item.id,
  item.displayName,
  inventoryBalance.quantityAvailable
FROM 
  item
  LEFT JOIN inventoryBalance ON item.id = inventoryBalance.item
WHERE 
  item.id = '12345'
```

POST to:
```
https://{{accountId}}.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql
```

Body:
```json
{
  "q": "SELECT item.id, item.displayName, inventoryBalance.quantityAvailable FROM item LEFT JOIN inventoryBalance ON item.id = inventoryBalance.item WHERE item.id = '12345'"
}
```

### Pattern 3: Handle Rate Limits

```javascript
// n8n Function Node - Rate limit handler
if ($json.error && $json.error.code === 'RATE_LIMIT_EXCEEDED') {
  // Wait and retry
  await new Promise(resolve => setTimeout(resolve, 60000)); // Wait 1 minute
  return { json: { retry: true } };
}
```

Use Kafka as a buffer:
```
Samsara → Kong → Kafka (buffer) → n8n (rate-limited consumer) → NetSuite
```

## Phase 4: n8n Workflow Examples

### Example 1: Samsara GPS → NetSuite Sales Order Update

```
1. Webhook Trigger (/webhook/samsara)
2. Extract delivery data
3. Query NetSuite for Sales Order (SuiteQL)
4. Update SO with delivery status
5. Log to Kafka (for audit)
6. Error Handler → DLQ
```

### Example 2: WMS Inventory Update → NetSuite

```
1. Kafka Trigger (topic: inventory)
2. Batch messages (every 100 or 5 minutes)
3. Transform to NetSuite format
4. POST to NetSuite Inventory Adjustment
5. Confirm back to WMS
```

## Phase 5: Troubleshooting

### Common Errors

**INVALID_LOGIN_CREDENTIALS**
- Check token hasn't expired
- Verify account ID is correct
- Ensure role has API access

**RATE_LIMIT_EXCEEDED**
- Implement exponential backoff
- Use Kafka as buffer
- Reduce concurrent requests

**INSUFFICIENT_PERMISSION**
- Check service account role permissions
- Verify access to specific record types

**SSS_REQUEST_LIMIT_EXCEEDED**
- Concurrent request limit hit
- Reduce parallel workflows
- Queue requests through Kafka

### Debugging Tips

1. **Test in NetSuite Postman**:
   - Use NetSuite's Postman collection
   - Verify auth works before n8n

2. **Enable detailed logging in n8n**:
   - Add Function nodes to log request/response
   - Log to Loki for analysis

3. **Monitor in Grafana**:
   - Track NetSuite API call rates
   - Alert on rate limit errors
   - Dashboard for success/failure rates

## Phase 6: Performance Optimization

### Use Kafka as Buffer

```
High-volume source → Kong → Kafka → n8n (controlled rate) → NetSuite
```

Benefits:
- Absorbs traffic spikes
- Respects NetSuite rate limits
- No data loss during outages
- Can replay failed messages

### Batch Operations

Instead of 100 individual updates:
```javascript
// Batch 100 inventory updates into 1 API call
const batch = items.slice(0, 100).map(item => ({
  operation: 'update',
  recordType: 'inventoryItem',
  id: item.id,
  fields: { quantityOnHand: item.quantity }
}));

// POST to /services/rest/record/v1/batch
```

### Caching

Cache frequently accessed data:
- Customer IDs
- Item IDs
- Subsidiary mappings

Use Redis or n8n's built-in cache.

## Inbound: NetSuite → Gateway (User Event Script template)

The sections above cover Gateway → NetSuite. For NetSuite to push events to
us — "a sales order was just created/updated" — install a User Event script
on the record types you care about, deployed as **After Submit**.

```javascript
/**
 * @NApiVersion 2.1
 * @NScriptType UserEventScript
 */
define(['N/https', 'N/runtime'], function (https, runtime) {
  var GATEWAY_URL = runtime.getCurrentScript().getParameter('custscript_gateway_url');
  var GATEWAY_KEY = runtime.getCurrentScript().getParameter('custscript_gateway_key');

  function afterSubmit(ctx) {
    if (ctx.type !== ctx.UserEventType.CREATE && ctx.type !== ctx.UserEventType.EDIT) return;

    var rec = ctx.newRecord;
    var payload = {
      source: 'netsuite',
      event_type:  ctx.type,           // 'create' | 'edit'
      record_type: rec.type,           // e.g. 'salesorder'
      id:          rec.id,
      tran_id:     rec.getValue({ fieldId: 'tranid' }),
      entity:      rec.getValue({ fieldId: 'entity' }),
      total:       rec.getValue({ fieldId: 'total' }),
      status:      rec.getValue({ fieldId: 'orderstatus' }),
      timestamp:   new Date().toISOString()
    };

    try {
      https.post({
        url: GATEWAY_URL,              // https://<gateway>/netsuite
        body: JSON.stringify(payload),
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key':    GATEWAY_KEY
        }
      });
    } catch (e) {
      // Never fail the NetSuite transaction because the webhook failed -
      // the Gateway has its own retry + DLQ. Log only.
      log.error('gateway webhook failed', e);
    }
  }
  return { afterSubmit: afterSubmit };
});
```

**Deployment:**

1. Upload to Documents → SuiteScripts.
2. Create a Script record (type: User Event) pointing at the file.
3. Add two script parameters:
   - `custscript_gateway_url` — `https://<your-gateway>/netsuite`
   - `custscript_gateway_key` — the key-auth value from the `netsuite-client` consumer in `kong/kong.yml`
4. Deploy against the record types you want to publish. Release; log level
   `Audit` while testing, `Error` in production.
5. User Event scripts run inline with the transaction — keep the call ≤2s.
   For heavier processing, buffer to a queue table and post asynchronously
   from a Scheduled Script.

On the Gateway side, add an n8n workflow on the `/netsuite` webhook that
validates `X-API-Key`, publishes to `orders` (or `netsuite-updates`) on
Kafka, and returns `204`. Downstream workflows (WMS pick, dispatch, Unigroup
booking) consume the event independently.

## Resources

- [NetSuite REST API Reference](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_1540391670.html)
- [SuiteQL Reference](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1558708800.html)
- [SuiteScript 2.x Reference](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/set_1502135122.html)
- [Token-Based Authentication Setup](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4247337262.html)

## Next Steps

1. Complete TBA setup in NetSuite
2. Test authentication with Postman
3. Create first n8n workflow (simple GET request)
4. Add error handling and retries
5. Implement rate limiting with Kafka
6. Build out full integration patterns

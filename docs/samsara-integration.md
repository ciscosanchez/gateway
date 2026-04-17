# Samsara Integration Setup

## Quick Start

### 1. Add Your Samsara API Token

Add to `.env`:

```bash
# Samsara API
SAMSARA_API_TOKEN=your_token_here
```

### 2. Samsara API Endpoints

Samsara uses REST API with Bearer token authentication:

**Base URL**: `https://api.samsara.com`

**Common Endpoints**:
- `/fleet/vehicles` - Get vehicle list
- `/fleet/vehicles/{id}/locations` - Get vehicle locations
- `/fleet/drivers` - Get drivers
- `/sensors/temperature` - Temperature sensor data

**Webhook Events**:
Samsara can send webhooks for:
- Vehicle location updates
- Door open/close events
- Engine diagnostics
- Geofence enter/exit
- Harsh events (braking, acceleration)

### 3. Register Webhook with Samsara

In Samsara dashboard:

1. Go to Settings → Developers → Webhooks
2. Create new webhook
3. URL: `https://your-public-host/samsara`  (HTTPS only — Kong rejects HTTP with a 301/426)
4. Configure the **Custom header** `X-API-Key` with the consumer key from `kong/kong.yml` (`samsara-client`).
5. Configure the **Signing secret** to match `SAMSARA_WEBHOOK_SECRET` in `.env`. Samsara will send `X-Samsara-Signature` which Kong's `pre-function` plugin verifies before the request reaches n8n.
4. Select events you want:
   - Vehicle locations
   - Geofence events
   - Diagnostic alerts
   - Door state changes

**For local testing**: Use ngrok to expose localhost
```bash
ngrok http 8000
# Use the ngrok URL in Samsara webhook settings
```

### 4. Test Samsara API

```bash
# Get vehicles
curl -X GET https://api.samsara.com/fleet/vehicles \
  -H "Authorization: Bearer $SAMSARA_API_TOKEN"

# Get vehicle locations
curl -X GET https://api.samsara.com/fleet/vehicles/locations \
  -H "Authorization: Bearer $SAMSARA_API_TOKEN"
```

## n8n Workflow: Samsara to Kafka to NetSuite

### Workflow 1: Receive Samsara Webhooks

**Nodes**:

1. **Webhook Trigger**
   - HTTP Method: POST
   - Path: `samsara`
   - Authentication: None (Kong handles this)

2. **Function: Parse Samsara Event**
   ```javascript
   // Extract relevant data from Samsara webhook
   const payload = $input.item.json;
   
   return {
     json: {
       event_type: payload.eventType,
       vehicle_id: payload.data?.vehicle?.id,
       driver_id: payload.data?.driver?.id,
       timestamp: payload.time,
       location: {
         lat: payload.data?.location?.latitude,
         lng: payload.data?.location?.longitude,
         address: payload.data?.location?.reverseGeo?.formattedLocation
       },
       raw: payload
     }
   };
   ```

3. **HTTP Request: Send to Kafka**
   - Method: POST
   - URL: `http://redpanda-0:28082/topics/samsara-events`
   - Body:
     ```json
     {
       "records": [{"value": {{ $json }}}]
     }
     ```

4. **Respond to Webhook**
   - Status Code: 200
   - Body: `{"success": true}`

### Workflow 2: Poll Samsara API (Scheduled)

For data Samsara doesn't push via webhooks:

1. **Schedule Trigger**
   - Interval: Every 5 minutes

2. **HTTP Request: Get Vehicle Locations**
   - Method: GET
   - URL: `https://api.samsara.com/fleet/vehicles/locations`
   - Authentication: Generic Credential Type
     - Header Auth
     - Name: `Authorization`
     - Value: `Bearer {{$env.SAMSARA_API_TOKEN}}`

3. **Function: Transform Response**
   ```javascript
   const vehicles = $input.item.json.data;
   
   return vehicles.map(vehicle => ({
     json: {
       vehicle_id: vehicle.id,
       name: vehicle.name,
       location: {
         lat: vehicle.latitude,
         lng: vehicle.longitude,
         heading: vehicle.heading,
         speed: vehicle.speed
       },
       timestamp: vehicle.time,
       odometer: vehicle.odometerMeters
     }
   }));
   ```

4. **Batch to Kafka**
   - Send all locations to `samsara-events` topic

### Workflow 3: Samsara → NetSuite (via Kafka)

1. **Kafka Trigger**
   - Topic: `samsara-events`
   - Bootstrap Servers: `redpanda-0:29092,redpanda-1:29092,redpanda-2:29092`
   - Consumer Group: `netsuite-delivery-updates`

2. **Function: Map to NetSuite Format**
   ```javascript
   const event = $input.item.json;
   
   // Only process delivery events
   if (event.event_type === 'gps' || event.event_type === 'geofence:exit') {
     return {
       json: {
         custrecord_delivery_vehicle: event.vehicle_id,
         custrecord_delivery_lat: event.location.lat,
         custrecord_delivery_lng: event.location.lng,
         custrecord_delivery_time: event.timestamp,
         custrecord_delivery_address: event.location.address
       }
     };
   }
   
   return null; // Skip other events
   ```

3. **HTTP Request: Update NetSuite**
   - Method: POST
   - URL: `https://{{$env.NETSUITE_ACCOUNT_ID}}.suitetalk.api.netsuite.com/services/rest/record/v1/customrecord_delivery_log`
   - Authentication: OAuth 1.0
   - Body: `{{ $json }}`

4. **Error Handler**
   - On error → send to `errors-dlq` topic

## Samsara Event Types

| Event Type | Description | Use Case |
|------------|-------------|----------|
| `gps` | Vehicle location update | Track deliveries in real-time |
| `geofence:enter` | Vehicle entered geofence | Arrival at customer |
| `geofence:exit` | Vehicle left geofence | Departure from warehouse |
| `door:open` / `door:close` | Cargo door events | Loading/unloading tracking |
| `engineHours` | Engine runtime | Maintenance scheduling |
| `diagnostics:fault` | Engine fault codes | Alert on vehicle issues |
| `harshEvent:acceleration` | Sudden acceleration | Driver safety monitoring |

## Testing

### 1. Send Test Webhook Locally

```bash
curl -X POST http://localhost:8000/samsara \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "gps",
    "time": "2026-04-16T18:30:00Z",
    "data": {
      "vehicle": {
        "id": "281474976710700",
        "name": "Truck #42"
      },
      "location": {
        "latitude": 37.7749,
        "longitude": -122.4194,
        "reverseGeo": {
          "formattedLocation": "123 Main St, San Francisco, CA"
        }
      }
    }
  }'
```

### 2. Check Kafka Topic

In **Redpanda Console** (http://localhost:8080):
- Navigate to Topics → `samsara-events`
- View the message that was sent

### 3. Test Samsara API Call

```bash
# Export your token
export SAMSARA_API_TOKEN="your_token_here"

# Get vehicles
curl https://api.samsara.com/fleet/vehicles \
  -H "Authorization: Bearer $SAMSARA_API_TOKEN" \
  | python3 -m json.tool
```

## Production Considerations

1. **Webhook Security**
   - Samsara signs webhooks with HMAC
   - Validate signature in Kong or n8n
   - Use HTTPS in production

2. **Rate Limiting**
   - Samsara API: 30 requests/second
   - Set Kong rate limit accordingly

3. **Deduplication**
   - Samsara may send duplicate events
   - Use Kafka consumer offsets
   - Add idempotency keys to NetSuite updates

4. **Error Handling**
   - Log failed events to `errors-dlq`
   - Set up alerts in Grafana
   - Implement retry logic

## Next Steps

1. Add your Samsara API token to `.env`
2. Create the webhook receiver workflow in n8n
3. Register webhook URL in Samsara dashboard (or use ngrok for testing)
4. Test with real Samsara events
5. Build the NetSuite sync workflow

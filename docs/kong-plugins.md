# Kong Plugin Examples

> **Note:** Gateway runs Kong in **DB-less / declarative mode**. The canonical,
> applied configuration lives in [`kong/kong.yml`](../kong/kong.yml). Every
> route already has `key-auth`, `rate-limiting` (Redis-backed), `cors`,
> `request-size-limiting`, `correlation-id`, a security-headers response
> transformer, `ip-restriction`, and `prometheus` attached globally, plus an
> HMAC pre-function on `/samsara`.
>
> The `curl`-against-the-admin-API examples below are kept as **reference
> only** — useful when you need to debug in a dev environment. Do not use them
> against production Kong; edit `kong/kong.yml` and run
> `./scripts/kong-setup.sh` to hot-reload instead.

This document provides production-ready examples of Kong plugins for securing and managing your API gateway.

## Authentication Plugins

### 1. API Key Authentication

**When to use:** Simple API authentication for external integrations

```bash
# Step 1: Create a consumer (represents an API client)
curl -X POST http://localhost:8001/consumers \
  --data "username=samsara-integration"

# Step 2: Enable key-auth plugin on the route
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=key-auth" \
  --data "config.key_names[]=X-API-Key"

# Step 3: Create an API key for the consumer
curl -X POST http://localhost:8001/consumers/samsara-integration/key-auth \
  --data "key=your-secure-random-key-here"

# Step 4: Use the API key
curl -H "X-API-Key: your-secure-random-key-here" \
  http://localhost:8000/samsara
```

### 2. OAuth 2.0

**When to use:** User-facing APIs with delegated authorization

```bash
# Enable OAuth 2.0
curl -X POST http://localhost:8001/routes/api-route/plugins \
  --data "name=oauth2" \
  --data "config.scopes[]=read" \
  --data "config.scopes[]=write" \
  --data "config.mandatory_scope=true"

# Create OAuth application
curl -X POST http://localhost:8001/consumers/my-app/oauth2 \
  --data "name=MyApp" \
  --data "client_id=my-client-id" \
  --data "client_secret=my-client-secret" \
  --data "redirect_uris[]=https://myapp.com/callback"
```

### 3. Basic Authentication

**When to use:** Internal services, simple use cases

```bash
# Enable Basic Auth
curl -X POST http://localhost:8001/routes/internal-route/plugins \
  --data "name=basic-auth" \
  --data "config.hide_credentials=true"

# Create credentials
curl -X POST http://localhost:8001/consumers/internal-service/basic-auth \
  --data "username=service" \
  --data "password=secure-password"
```

---

## Rate Limiting Plugins

### 1. Rate Limiting (Local)

**Use for:** Single-instance deployments

```bash
# Limit to 100 requests per minute per consumer
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=rate-limiting" \
  --data "config.minute=100" \
  --data "config.hour=1000" \
  --data "config.policy=local" \
  --data "config.fault_tolerant=true"
```

### 2. Rate Limiting Advanced (Distributed)

**Use for:** Multi-instance deployments (requires Redis)

```bash
# Distributed rate limiting with Redis
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=rate-limiting" \
  --data "config.minute=100" \
  --data "config.policy=redis" \
  --data "config.redis_host=redis" \
  --data "config.redis_port=6379" \
  --data "config.redis_timeout=2000"
```

### 3. Request Size Limiting

**Use for:** Prevent large payload attacks

```bash
# Limit request body to 10MB
curl -X POST http://localhost:8001/routes/upload-route/plugins \
  --data "name=request-size-limiting" \
  --data "config.allowed_payload_size=10"
```

---

## Security Plugins

### 1. IP Restriction

**Use for:** Whitelist/blacklist IP addresses

```bash
# Allow only specific IPs
curl -X POST http://localhost:8001/routes/admin-route/plugins \
  --data "name=ip-restriction" \
  --data "config.allow=10.0.0.0/8" \
  --data "config.allow=192.168.1.100"

# Deny specific IPs
curl -X POST http://localhost:8001/routes/public-route/plugins \
  --data "name=ip-restriction" \
  --data "config.deny=1.2.3.4"
```

### 2. CORS (Cross-Origin Resource Sharing)

**Use for:** Browser-based API access

```bash
curl -X POST http://localhost:8001/routes/api-route/plugins \
  --data "name=cors" \
  --data "config.origins=https://yourapp.com" \
  --data "config.methods=GET,POST,PUT,DELETE" \
  --data "config.headers=Accept,Content-Type,Authorization" \
  --data "config.max_age=3600" \
  --data "config.credentials=true"
```

### 3. Bot Detection

**Use for:** Block bot traffic

```bash
curl -X POST http://localhost:8001/routes/public-route/plugins \
  --data "name=bot-detection" \
  --data "config.allow[]=googlebot" \
  --data "config.allow[]=bingbot" \
  --data "config.deny[]=scrapy"
```

---

## Traffic Control Plugins

### 1. Request Termination

**Use for:** Temporary API shutdown, maintenance mode

```bash
# Return 503 Service Unavailable
curl -X POST http://localhost:8001/routes/api-route/plugins \
  --data "name=request-termination" \
  --data "config.status_code=503" \
  --data "config.message=Service temporarily unavailable"
```

### 2. Response Transformer

**Use for:** Add/remove headers, modify responses

```bash
# Add security headers
curl -X POST http://localhost:8001/routes/api-route/plugins \
  --data "name=response-transformer" \
  --data "config.add.headers=X-Frame-Options:DENY" \
  --data "config.add.headers=X-Content-Type-Options:nosniff" \
  --data "config.remove.headers=Server"
```

### 3. Request Transformer

**Use for:** Modify incoming requests

```bash
# Add authentication header
curl -X POST http://localhost:8001/routes/api-route/plugins \
  --data "name=request-transformer" \
  --data "config.add.headers=X-Forwarded-By:Kong" \
  --data "config.remove.headers=X-Internal-Header"
```

---

## Logging & Monitoring Plugins

### 1. File Log

**Use for:** Local logging

```bash
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=file-log" \
  --data "config.path=/var/log/kong/samsara.log"
```

### 2. HTTP Log

**Use for:** Send logs to external service

```bash
# Send to logging service
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=http-log" \
  --data "config.http_endpoint=https://logs.yourservice.com/api/logs" \
  --data "config.method=POST" \
  --data "config.timeout=1000" \
  --data "config.keepalive=60000"
```

### 3. Prometheus

**Use for:** Metrics collection

```bash
# Enable Prometheus metrics
curl -X POST http://localhost:8001/plugins \
  --data "name=prometheus"

# Metrics available at: http://localhost:8001/metrics
```

### 4. Datadog

**Use for:** APM and monitoring

```bash
curl -X POST http://localhost:8001/routes/api-route/plugins \
  --data "name=datadog" \
  --data "config.host=datadog-agent" \
  --data "config.port=8125"
```

---

## Complete Production Example

Here's a complete example for securing the Samsara integration:

```bash
#!/bin/bash

# Variables
KONG_ADMIN="http://localhost:8001"
ROUTE_ID="samsara-route"
API_KEY="$(openssl rand -hex 32)"

echo "Securing Samsara route..."

# 1. API Key Authentication
curl -X POST $KONG_ADMIN/consumers \
  --data "username=samsara-client"

curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=key-auth" \
  --data "config.key_names[]=X-API-Key"

curl -X POST $KONG_ADMIN/consumers/samsara-client/key-auth \
  --data "key=$API_KEY"

echo "API Key: $API_KEY"

# 2. Rate Limiting (100/min per key)
curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=rate-limiting" \
  --data "config.minute=100" \
  --data "config.policy=local"

# 3. IP Restriction (whitelist)
curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=ip-restriction" \
  --data "config.allow=10.0.0.0/8"

# 4. Request Size Limiting (1MB)
curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=request-size-limiting" \
  --data "config.allowed_payload_size=1"

# 5. CORS
curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=cors" \
  --data "config.origins=*" \
  --data "config.methods=GET,POST,PUT,DELETE"

# 6. Response Headers (security)
curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=response-transformer" \
  --data "config.add.headers=X-Frame-Options:DENY" \
  --data "config.add.headers=X-Content-Type-Options:nosniff" \
  --data "config.remove.headers=Server"

# 7. Logging
curl -X POST $KONG_ADMIN/routes/$ROUTE_ID/plugins \
  --data "name=file-log" \
  --data "config.path=/var/log/kong/samsara.log"

echo "✅ Samsara route secured!"
echo "Use header: X-API-Key: $API_KEY"
```

Save this as `scripts/secure-kong-routes.sh` and run it to apply all security plugins.

---

## Plugin Priority

Plugins execute in order of priority. Higher numbers run first:

| Priority | Plugin | Purpose |
|----------|--------|---------|
| 1000+ | IP Restriction | Block bad IPs early |
| 1000 | Bot Detection | Block bots |
| 1000 | Rate Limiting | Prevent abuse |
| 1000 | Request Size Limiting | Prevent large payloads |
| 1000 | Key Auth / OAuth | Authenticate |
| 900 | CORS | Handle preflight |
| 800 | Request Transformer | Modify request |
| 600 | Response Transformer | Modify response |
| 1 | Logging | Log everything |

---

## Testing Your Plugins

```bash
# Test API key auth
curl -H "X-API-Key: your-key" http://localhost:8000/samsara

# Test rate limiting (should get 429 after limit)
for i in {1..105}; do 
  curl -H "X-API-Key: your-key" http://localhost:8000/samsara
done

# Test IP restriction
curl http://localhost:8000/samsara  # Should fail if IP not whitelisted

# Check plugin configuration
curl http://localhost:8001/routes/samsara-route/plugins
```

---

## Removing Plugins

```bash
# List plugins on a route
curl http://localhost:8001/routes/samsara-route/plugins

# Delete a plugin
curl -X DELETE http://localhost:8001/plugins/{plugin-id}

# Disable without deleting
curl -X PATCH http://localhost:8001/plugins/{plugin-id} \
  --data "enabled=false"
```

---

## Resources

- [Kong Plugin Hub](https://docs.konghq.com/hub/)
- [Kong Admin API Reference](https://docs.konghq.com/gateway/latest/admin-api/)
- [Plugin Development Guide](https://docs.konghq.com/gateway/latest/plugin-development/)

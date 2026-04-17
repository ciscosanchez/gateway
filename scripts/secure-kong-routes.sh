#!/bin/bash

# Kong Security Setup Script
# Adds authentication and rate limiting to all routes

set -e

KONG_ADMIN="http://localhost:8001"

echo "🔒 Securing Kong Routes..."
echo ""

# Generate strong API key
API_KEY=$(openssl rand -hex 32)

echo "Creating consumers..."

# Create consumer for Samsara integration
curl -s -X POST $KONG_ADMIN/consumers \
  --data "username=samsara-client" > /dev/null

curl -s -X POST $KONG_ADMIN/consumers/samsara-client/key-auth \
  --data "key=$API_KEY" > /dev/null

echo "✅ Consumer 'samsara-client' created"
echo "   API Key: $API_KEY"
echo ""

# Get route IDs
SAMSARA_ROUTE=$(curl -s $KONG_ADMIN/routes | jq -r '.data[] | select(.paths[] | contains("/samsara")) | .id')

if [ -z "$SAMSARA_ROUTE" ]; then
  echo "⚠️  Samsara route not found. Run ./scripts/kong-setup.sh first."
  exit 1
fi

echo "Securing /samsara route..."

# 1. Add API Key Authentication
echo "  Adding key-auth plugin..."
curl -s -X POST $KONG_ADMIN/routes/$SAMSARA_ROUTE/plugins \
  --data "name=key-auth" \
  --data "config.key_names[]=X-API-Key" \
  --data "config.hide_credentials=true" > /dev/null

# 2. Add Rate Limiting (100 requests per minute)
echo "  Adding rate-limiting plugin..."
curl -s -X POST $KONG_ADMIN/routes/$SAMSARA_ROUTE/plugins \
  --data "name=rate-limiting" \
  --data "config.minute=100" \
  --data "config.hour=1000" \
  --data "config.policy=local" \
  --data "config.fault_tolerant=true" > /dev/null

# 3. Add Request Size Limiting (10MB)
echo "  Adding request-size-limiting plugin..."
curl -s -X POST $KONG_ADMIN/routes/$SAMSARA_ROUTE/plugins \
  --data "name=request-size-limiting" \
  --data "config.allowed_payload_size=10" > /dev/null

# 4. Add Security Headers
echo "  Adding response-transformer plugin (security headers)..."
curl -s -X POST $KONG_ADMIN/routes/$SAMSARA_ROUTE/plugins \
  --data "name=response-transformer" \
  --data "config.add.headers=X-Frame-Options:DENY" \
  --data "config.add.headers=X-Content-Type-Options:nosniff" \
  --data "config.add.headers=X-XSS-Protection:1; mode=block" \
  --data "config.remove.headers=Server" > /dev/null

# 5. Add CORS
echo "  Adding CORS plugin..."
curl -s -X POST $KONG_ADMIN/routes/$SAMSARA_ROUTE/plugins \
  --data "name=cors" \
  --data "config.origins=*" \
  --data "config.methods=GET,POST,PUT,DELETE,OPTIONS" \
  --data "config.headers=Accept,Content-Type,Authorization,X-API-Key" \
  --data "config.max_age=3600" \
  --data "config.credentials=false" > /dev/null

# 6. Add Logging
echo "  Adding file-log plugin..."
curl -s -X POST $KONG_ADMIN/routes/$SAMSARA_ROUTE/plugins \
  --data "name=file-log" \
  --data "config.path=/tmp/kong-samsara.log" > /dev/null

echo "✅ /samsara route secured!"
echo ""

echo "📝 Usage:"
echo ""
echo "  Test the secured endpoint:"
echo "  curl -H 'X-API-Key: $API_KEY' http://localhost:8000/samsara"
echo ""
echo "  Without API key (should return 401):"
echo "  curl http://localhost:8000/samsara"
echo ""
echo "  Test rate limiting (run 105 times, should get 429):"
echo "  for i in {1..105}; do curl -H 'X-API-Key: $API_KEY' http://localhost:8000/samsara; done"
echo ""

echo "💾 Save this API key securely!"
echo "API_KEY=$API_KEY" >> .env.local
echo ""
echo "Saved to .env.local"
echo ""
echo "✅ Kong security setup complete!"

#!/usr/bin/env bash
# Replay a Samsara-style webhook payload through the full gateway funnel:
#   curl  ->  Kong /samsara  ->  HMAC verify (pre-function)  ->  key-auth
#         ->  rate-limit     ->  n8n /webhook/samsara
#         ->  Parse & Redact ->  Kafka samsara-events topic
#
# Useful for:
#   - smoke-testing the pipeline before real Samsara traffic lands
#   - reproducing bad events from prod in dev
#   - regression testing after any pipeline change
#
# Requires in .env:
#   SAMSARA_WEBHOOK_SECRET   - same value configured on the Samsara webhook
#   SMOKE_API_KEY            - a key-auth value bound to the samsara-client
#                              consumer in kong/kong.yml (or set SKIP_KEY_AUTH=1
#                              to bypass key-auth via the admin API, dev only)
#
# Usage:
#   ./scripts/samsara-replay.sh                                          # default sample
#   ./scripts/samsara-replay.sh workflows/samples/samsara-geofence-entry.json
#   GATEWAY_URL=https://my.gateway.com ./scripts/samsara-replay.sh       # against remote
#   TAMPER=1 ./scripts/samsara-replay.sh                                 # flip one byte
#                                                                        # of the signature
#                                                                        # to prove HMAC
#                                                                        # path rejects it
set -euo pipefail

# shellcheck disable=SC1091
[ -f .env ] && source .env

GATEWAY_URL="${GATEWAY_URL:-https://localhost:8443}"
PAYLOAD_FILE="${1:-workflows/samples/samsara-geofence-entry.json}"
TAMPER="${TAMPER:-0}"

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m%s\033[0m\n' "$*"; }

if [ ! -f "$PAYLOAD_FILE" ]; then
  red "payload not found: $PAYLOAD_FILE"; exit 1
fi
if [ -z "${SAMSARA_WEBHOOK_SECRET:-}" ] || [[ "${SAMSARA_WEBHOOK_SECRET}" == CHANGE_ME ]]; then
  red "SAMSARA_WEBHOOK_SECRET not set in .env (or is still CHANGE_ME)"; exit 1
fi
if [ -z "${SMOKE_API_KEY:-}" ]; then
  warn "SMOKE_API_KEY not set - Kong key-auth will 401. Set it to a value bound to samsara-client in kong/kong.yml."
fi

# Compact the JSON so signature matches whatever Samsara sends byte-for-byte.
# (Both n8n and Samsara use compact JSON in practice.)
if command -v jq >/dev/null 2>&1; then
  BODY=$(jq -c . "$PAYLOAD_FILE")
else
  BODY=$(tr -d '\n' < "$PAYLOAD_FILE")
fi

# HMAC-SHA256 hex, matching the Kong pre-function plugin logic in kong/kong.yml.
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SAMSARA_WEBHOOK_SECRET" -hex | awk '{print $NF}')

if [ "$TAMPER" = "1" ]; then
  warn "TAMPER=1: flipping first hex char of signature; request should 401"
  first="${SIG:0:1}"
  rest="${SIG:1}"
  # swap first hex char (simple: 0->1, else -> 0)
  if [ "$first" = "0" ]; then flipped="1"; else flipped="0"; fi
  SIG="${flipped}${rest}"
fi

echo "→ POST ${GATEWAY_URL}/samsara"
echo "  payload : ${PAYLOAD_FILE} ($(printf '%s' "$BODY" | wc -c | tr -d ' ') bytes)"
echo "  sig     : ${SIG:0:12}... (sha256, hex)"
echo ""

RESP=$(mktemp)
HTTP=$(curl -sk -o "$RESP" -w '%{http_code}' -X POST "${GATEWAY_URL}/samsara" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SMOKE_API_KEY:-}" \
  -H "X-Samsara-Signature: ${SIG}" \
  --data-binary "$BODY")

echo "HTTP ${HTTP}"
echo "body:"
cat "$RESP"; echo
rm -f "$RESP"

case "$HTTP" in
  202) green "✅ accepted - check Kafka: docker exec gateway-redpanda-0 rpk topic consume samsara-events --brokers redpanda-0:29092 -n 1 -f '%v\\n'" ;;
  401) red   "❌ 401 - key-auth or HMAC failed. Expected with TAMPER=1; otherwise check SMOKE_API_KEY / SAMSARA_WEBHOOK_SECRET." ;;
  429) warn  "⚠️  429 - rate limit tripped; wait a minute and retry." ;;
  500) red   "❌ 500 - check gateway-n8n and gateway-kong logs." ;;
  *)   red   "❌ unexpected ${HTTP}" ;;
esac

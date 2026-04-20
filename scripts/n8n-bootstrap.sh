#!/usr/bin/env bash
# Idempotent bootstrap for the n8n main container.
#
# What it does, in order:
#   1. Wait for n8n HTTP to be ready
#   2. Set up the owner account if it doesn't exist yet (one-time)
#   3. Log in and get a session cookie (JWT in n8n-auth cookie)
#   4. Create the Redpanda Kafka credential if missing
#   5. For every workflow in workflows/*.json:
#        - import via `n8n import:workflow` (CLI is safe; it's the activation that's broken)
#        - fetch the workflow via REST, rewrite kafka credential IDs to the real one
#        - PATCH with active:true — this is the call that actually populates
#          webhook_entity (see n8n issue #21614). CLI `update:workflow --active=true`
#          marks the flag but does NOT register the webhook, so POSTs 404.
#   6. Verify webhook_entity in Postgres has a row for each webhook node.
#
# Re-runnable: if owner exists -> skip; if creds exist -> reuse; if workflow
# exists -> PATCH-only.
#
# Secrets read from .env (none of this should live in source):
#   N8N_OWNER_EMAIL              owner login
#   N8N_OWNER_PASSWORD           owner password (set when owner is first created)
#   N8N_OWNER_FIRST_NAME         used only on first-run owner setup (default: Dev)
#   N8N_OWNER_LAST_NAME          used only on first-run owner setup (default: Owner)
#   POSTGRES_PASSWORD            to read webhook_entity for verification
#
# Usage:
#   ./scripts/n8n-bootstrap.sh                          # bootstrap + verify
#   SKIP_VERIFY=1 ./scripts/n8n-bootstrap.sh            # skip webhook_entity sanity check
#
set -euo pipefail

# shellcheck disable=SC1091
[ -f .env ] && source .env

N8N_INTERNAL_URL="${N8N_INTERNAL_URL:-http://n8n:5678}"
COMPOSE_NETWORK="${COMPOSE_NETWORK:-gateway_gateway-network}"
WORKFLOWS_DIR="${WORKFLOWS_DIR:-workflows}"
KAFKA_CRED_NAME="${KAFKA_CRED_NAME:-Redpanda (internal)}"
KAFKA_BROKERS="${KAFKA_BROKERS:-redpanda-0:29092}"
# Workflow JSONs reference credentials by ID. When the stack is first
# bootstrapped we don't know n8n's credential ID yet, so we rewrite any
# kafka credential reference to the one we create here. The literal ID
# used in source is this marker — anything matching gets substituted.
WORKFLOW_KAFKA_CRED_MARKER="${WORKFLOW_KAFKA_CRED_MARKER:-redpanda}"

red()    { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*" >&2; }
info()   { printf '→ %s\n' "$*" >&2; }

# Helper that runs curl inside the docker network so we talk to n8n by hostname.
n8n_curl() {
  docker run --rm --network="$COMPOSE_NETWORK" \
    -v /tmp:/tmp \
    curlimages/curl:latest -s "$@"
}

for cmd in docker jq openssl; do
  command -v "$cmd" >/dev/null 2>&1 || { red "missing: $cmd"; exit 1; }
done

: "${N8N_OWNER_EMAIL:?N8N_OWNER_EMAIL is required}"
: "${N8N_OWNER_PASSWORD:?N8N_OWNER_PASSWORD is required (min 8 chars, 1 number, 1 capital)}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

COOKIE_FILE="$(mktemp)"
trap 'rm -f "$COOKIE_FILE"' EXIT

# ---------------------------------------------------------------------------
# 1. Wait for n8n
# ---------------------------------------------------------------------------
info "waiting for n8n at ${N8N_INTERNAL_URL}"
for i in $(seq 1 60); do
  code=$(n8n_curl -o /dev/null -w '%{http_code}' "${N8N_INTERNAL_URL}/healthz" || echo "000")
  [ "$code" = "200" ] && { green "n8n is ready"; break; }
  sleep 2
  [ "$i" = 60 ] && { red "n8n did not become ready in 120s"; exit 1; }
done

# ---------------------------------------------------------------------------
# 2. Owner setup (first-run only)
# ---------------------------------------------------------------------------
# /rest/owner/setup doesn't expose a clean check endpoint; the simpler
# probe is to attempt login. 200 => owner exists and password matches;
# 401 => owner missing OR password mismatch (we handle both below).
login_probe=$(n8n_curl -o /dev/null -w '%{http_code}' \
  -X POST "${N8N_INTERNAL_URL}/rest/login" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg e "$N8N_OWNER_EMAIL" --arg p "$N8N_OWNER_PASSWORD" '{email:$e,password:$p}')")

case "$login_probe" in
  200)
    info "owner exists + password matches — skipping setup"
    ;;
  401)
    info "owner not set up or password mismatch — attempting first-run setup"
    body=$(jq -nc \
      --arg e "$N8N_OWNER_EMAIL" \
      --arg p "$N8N_OWNER_PASSWORD" \
      --arg f "${N8N_OWNER_FIRST_NAME:-Dev}" \
      --arg l "${N8N_OWNER_LAST_NAME:-Owner}" \
      '{email:$e, password:$p, firstName:$f, lastName:$l}')
    setup_code=$(n8n_curl -o /tmp/n8n-setup.out -w '%{http_code}' \
      -X POST "${N8N_INTERNAL_URL}/rest/owner/setup" \
      -H "Content-Type: application/json" \
      -d "$body")
    if [ "$setup_code" = "200" ]; then
      green "owner created"
    else
      red "owner setup failed (HTTP $setup_code):"
      cat /tmp/n8n-setup.out >&2
      red "If the owner exists with a different password, reset it:"
      red "  docker exec -e PGPASSWORD=\$POSTGRES_PASSWORD gateway-postgres \\"
      red "    psql -U gateway -d gateway -c \"DELETE FROM \\\"user\\\";\""
      red "  ... then re-run this script."
      exit 1
    fi
    ;;
  *)
    red "unexpected login probe response: $login_probe"; exit 1;;
esac

# ---------------------------------------------------------------------------
# 3. Log in and grab session cookie
# ---------------------------------------------------------------------------
info "logging in"
COOKIE=$(n8n_curl -D - -o /dev/null \
  -X POST "${N8N_INTERNAL_URL}/rest/login" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg e "$N8N_OWNER_EMAIL" --arg p "$N8N_OWNER_PASSWORD" '{email:$e,password:$p}')" \
  | awk -F'[= ;]' '/^Set-Cookie:.*n8n-auth/ {print $3}' | tr -d '\r')

[ -z "$COOKIE" ] && { red "login succeeded but no cookie returned"; exit 1; }
echo "$COOKIE" > "$COOKIE_FILE"

# ---------------------------------------------------------------------------
# 4. Create Redpanda Kafka credential if missing
# ---------------------------------------------------------------------------
info "ensuring kafka credential '${KAFKA_CRED_NAME}' exists"
creds=$(n8n_curl -b "n8n-auth=$COOKIE" "${N8N_INTERNAL_URL}/rest/credentials")
KAFKA_CRED_ID=$(echo "$creds" | jq -r --arg n "$KAFKA_CRED_NAME" \
  '.data[]? | select(.name == $n and .type == "kafka") | .id' | head -1)

if [ -z "$KAFKA_CRED_ID" ]; then
  cred_body=$(jq -nc \
    --arg name "$KAFKA_CRED_NAME" \
    --arg brokers "$KAFKA_BROKERS" \
    '{
      name:$name,
      type:"kafka",
      nodesAccess:[{nodeType:"n8n-nodes-base.kafka"}],
      data:{clientId:"n8n", brokers:$brokers, ssl:false, authentication:false,
            saslMechanism:"plain", username:"", password:""}
    }')
  resp=$(n8n_curl -b "n8n-auth=$COOKIE" -X POST \
    "${N8N_INTERNAL_URL}/rest/credentials" \
    -H "Content-Type: application/json" -d "$cred_body")
  KAFKA_CRED_ID=$(echo "$resp" | jq -r '.data.id')
  [ -z "$KAFKA_CRED_ID" ] || [ "$KAFKA_CRED_ID" = "null" ] && {
    red "failed to create kafka credential: $resp"; exit 1; }
  green "created kafka credential id=${KAFKA_CRED_ID}"
else
  info "kafka credential already exists id=${KAFKA_CRED_ID}"
fi

# ---------------------------------------------------------------------------
# 5. Import + activate each workflow
# ---------------------------------------------------------------------------
imported=0
activated=0
for wf in "$WORKFLOWS_DIR"/*.json; do
  [ -f "$wf" ] || continue
  name=$(jq -r '.name' "$wf")
  info "workflow: ${name}"

  # Check if a workflow with this name is already in n8n
  existing=$(n8n_curl -b "n8n-auth=$COOKIE" \
    "${N8N_INTERNAL_URL}/rest/workflows" \
    | jq -r --arg n "$name" '.data[]? | select(.name==$n) | .id' | head -1)

  if [ -z "$existing" ]; then
    # Import via CLI. Wrap in array + set active:false to satisfy NOT NULL.
    tmp_in="/tmp/wf-in-$$.json"
    jq "[. + {active:false}]" "$wf" > "$tmp_in"
    docker cp "$tmp_in" gateway-n8n:/tmp/wf-in.json >/dev/null
    docker exec -u root gateway-n8n n8n import:workflow --input=/tmp/wf-in.json \
      >/dev/null 2>&1 || { red "import failed for $wf"; rm -f "$tmp_in"; exit 1; }
    rm -f "$tmp_in"
    existing=$(n8n_curl -b "n8n-auth=$COOKIE" \
      "${N8N_INTERNAL_URL}/rest/workflows" \
      | jq -r --arg n "$name" '.data[]? | select(.name==$n) | .id' | head -1)
    [ -z "$existing" ] && { red "import succeeded but workflow not visible"; exit 1; }
    imported=$((imported+1))
    info "  imported id=${existing}"
  else
    info "  already present id=${existing}"
  fi

  # Fetch, rewrite kafka cred ids, PATCH with active:true.
  current=$(n8n_curl -b "n8n-auth=$COOKIE" \
    "${N8N_INTERNAL_URL}/rest/workflows/${existing}")
  patch_body=$(echo "$current" | jq \
    --arg cid "$KAFKA_CRED_ID" \
    --arg marker "$WORKFLOW_KAFKA_CRED_MARKER" '
      .data
      | {
          name,
          connections,
          settings,
          active: true,
          nodes: (.nodes | map(
            if .credentials.kafka and (.credentials.kafka.id == $marker or .credentials.kafka.id == null)
              then .credentials.kafka.id = $cid
              else .
            end
          ))
        }')

  patch_code=$(echo "$patch_body" \
    | n8n_curl -o /tmp/n8n-patch.out -w '%{http_code}' -b "n8n-auth=$COOKIE" \
        -X PATCH "${N8N_INTERNAL_URL}/rest/workflows/${existing}" \
        -H "Content-Type: application/json" --data-binary @-)
  if [ "$patch_code" = "200" ]; then
    activated=$((activated+1))
    green "  activated"
  else
    red "  PATCH failed (HTTP $patch_code):"
    cat /tmp/n8n-patch.out >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# 6. Verify webhook_entity
# ---------------------------------------------------------------------------
if [ "${SKIP_VERIFY:-0}" != "1" ]; then
  info "verifying webhook_entity"
  rows=$(docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" gateway-postgres \
    psql -U gateway -d gateway -tAc \
    "SELECT count(*) FROM webhook_entity;")
  if [ "$rows" -gt 0 ]; then
    green "webhook_entity has ${rows} registered webhook(s)"
  else
    red "webhook_entity is empty — activation did not register webhooks"
    red "check: docker logs gateway-n8n"
    exit 1
  fi
fi

green "✅ bootstrap complete — imported=${imported} activated=${activated}"

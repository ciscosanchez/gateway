#!/usr/bin/env bash
# Load-test the full Samsara ingress path end-to-end:
#
#   curl -> Kong (key-auth + HMAC verify + rate-limit + ip-allow)
#        -> n8n webhook -> Function node -> Kafka produce
#
# Stages climb until something saturates. Between stages we snapshot
# `docker stats` and Kafka consumer lag, so the report shows WHERE the
# bottleneck was, not just that one existed.
#
# Prereqs:
#   - `hey` installed (brew install hey) — or `oha` as fallback.
#   - docker-compose stack has been bootstrapped once:
#       docker compose up -d
#       ./scripts/n8n-bootstrap.sh
#   - .env has SAMSARA_WEBHOOK_SECRET, SMOKE_API_KEY, POSTGRES_PASSWORD set.
#
# How the test relaxes Kong (without committing wide-open config):
#   We generate /tmp/kong.loadtest.yml from kong/kong.yml with three
#   substitutions (rate-limit, IP allowlist, samsara-client API key) and
#   bring Kong up with docker-compose.loadtest.yml overlay. On exit (even
#   on Ctrl+C) we revert Kong to the canonical config. No files in-tree
#   are modified.
#
# Usage:
#   ./scripts/load-test.sh                         # default stages
#   DURATION=30 STAGES="100 500 1000" ./scripts/load-test.sh
#   OUT=./my-report.md ./scripts/load-test.sh
#   ./scripts/load-test.sh --prep-only             # just generate overlay
#                                                  # config + print how to
#                                                  # bring Kong up manually.
set -euo pipefail

# shellcheck disable=SC1091
[ -f .env ] && source .env

TS="$(date +%Y%m%d_%H%M%S)"
OUT="${OUT:-./load-test-${TS}.md}"
GATEWAY_URL="${GATEWAY_URL:-https://localhost:8443}"
ENDPOINT="${ENDPOINT:-${GATEWAY_URL}/samsara}"
DURATION="${DURATION:-30}"
STAGES="${STAGES:-50 200 500 1000 2000}"
CONCURRENCY="${CONCURRENCY:-100}"
PAYLOAD_FILE="${PAYLOAD_FILE:-workflows/samples/samsara-geofence-entry.json}"

KONG_LOADTEST_YML="/tmp/kong.loadtest.yml"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.loadtest.yml)
# The dev API key the loadtest kong.yml is rewritten to use. Must match
# what the test sends as X-API-Key. We source it from SMOKE_API_KEY so
# a single value is responsible for both sides.
LOADTEST_KEY="${SMOKE_API_KEY:-}"

red()    { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*" >&2; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
info()   { printf '→ %s\n' "$*" >&2; }

# --- prerequisites --------------------------------------------------------

LOADER=""
if   command -v hey >/dev/null 2>&1; then LOADER="hey"
elif command -v oha >/dev/null 2>&1; then LOADER="oha"
else
  red "Neither 'hey' nor 'oha' found."
  cat <<'EOF' >&2
  macOS:  brew install hey
          brew install oha
  Linux:  go install github.com/rakyll/hey@latest
EOF
  exit 1
fi

for cmd in openssl jq docker sed; do
  command -v "$cmd" >/dev/null 2>&1 || { red "missing: $cmd"; exit 1; }
done

: "${SAMSARA_WEBHOOK_SECRET:?not set (or still CHANGE_ME) - Kong HMAC will reject every request}"
: "${SMOKE_API_KEY:?not set - required as the dev key for loadtest overlay and X-API-Key header}"
: "${POSTGRES_PASSWORD:?not set}"

case "${SAMSARA_WEBHOOK_SECRET}" in CHANGE_ME*|REPLACE_ME*|"")
  red "SAMSARA_WEBHOOK_SECRET is a placeholder. Set a real value in .env."; exit 1;;
esac
case "${SMOKE_API_KEY}" in CHANGE_ME*|REPLACE_ME*|"")
  red "SMOKE_API_KEY is a placeholder. Set a real value in .env."; exit 1;;
esac

[ -f "$PAYLOAD_FILE" ] || { red "payload missing: $PAYLOAD_FILE"; exit 1; }
[ -f kong/kong.yml ] || { red "kong/kong.yml missing (run from repo root)"; exit 1; }
[ -f docker-compose.loadtest.yml ] || { red "docker-compose.loadtest.yml missing"; exit 1; }

if ! docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -q "gateway-kong.*Up"; then
  red "gateway-kong is not running - start the stack first (docker compose up -d)"
  exit 1
fi

# --- build the loadtest kong.yml ------------------------------------------

info "generating ${KONG_LOADTEST_YML} from kong/kong.yml"
# One sed pipeline. Each substitution is precise enough that we don't
# risk matching unrelated text. If kong/kong.yml is ever restructured so
# these anchors change, the grep checks below will fail loudly.
sed \
  -e 's/^      minute: 100$/      minute: 10000000/' \
  -e 's/^      hour: 5000$/      hour: 100000000/' \
  -e "s|^      - key: REPLACE_ME_samsara_api_key\$|      - key: ${LOADTEST_KEY}|" \
  -e 's|^        - "127.0.0.1/32"$|        - "0.0.0.0/0"|' \
  -e '/^        - "10\.0\.0\.0\/8"$/d' \
  -e '/^        - "172\.16\.0\.0\/12"$/d' \
  -e '/^        - "192\.168\.0\.0\/16"$/d' \
  kong/kong.yml > "$KONG_LOADTEST_YML"

# Verify the substitutions landed — fail fast if not.
for marker in "minute: 10000000" "hour: 100000000" "0.0.0.0/0" "key: ${LOADTEST_KEY}"; do
  grep -q "$marker" "$KONG_LOADTEST_YML" || {
    red "load-test config generation failed: missing '${marker}'"
    red "kong/kong.yml may have been restructured; update the sed block"
    exit 1
  }
done
green "overlay kong.yml generated ($(wc -l < "$KONG_LOADTEST_YML") lines)"

if [ "${1:-}" = "--prep-only" ]; then
  cat <<EOF
prep complete. To bring Kong up with load-test config:
  docker compose ${COMPOSE_FILES[*]} up -d kong
to revert:
  docker compose up -d kong
EOF
  exit 0
fi

# --- switch Kong to the loadtest overlay, with revert-on-exit trap --------

revert_kong() {
  info "reverting Kong to canonical kong/kong.yml"
  docker compose up -d --force-recreate kong >/dev/null 2>&1 || true
  rm -f "$KONG_LOADTEST_YML"
}
trap revert_kong EXIT INT TERM

info "bringing Kong up with loadtest overlay"
docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate kong >/dev/null 2>&1
# Wait for healthy
for _ in $(seq 1 30); do
  hs=$(docker inspect gateway-kong --format '{{.State.Health.Status}}' 2>/dev/null || echo "")
  [ "$hs" = "healthy" ] && break
  sleep 2
done
[ "$hs" = "healthy" ] || { red "Kong did not become healthy under loadtest overlay"; exit 1; }
green "Kong ready on loadtest overlay"

# Pre-sign one request — same body & sig replayed N times. This tests the
# server-side HMAC verify cost, not client HMAC cost. Real Samsara traffic
# is per-event signed, but the server-side verification cost is constant
# per request so this is representative.
BODY=$(jq -c . "$PAYLOAD_FILE")
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SAMSARA_WEBHOOK_SECRET" -hex | awk '{print $NF}')

info "loader  : $LOADER"
info "endpoint: $ENDPOINT"
info "payload : $PAYLOAD_FILE ($(printf '%s' "$BODY" | wc -c | tr -d ' ') bytes)"
info "stages  : $STAGES req/s × ${DURATION}s each, c=${CONCURRENCY}"
info "report  : $OUT"

# Warm-up: confirm the pipeline returns 2xx before we measure anything.
warm_code=$(curl -sk -o /dev/null -w '%{http_code}' -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SMOKE_API_KEY}" \
  -H "X-Samsara-Signature: ${SIG}" \
  -d "$BODY")
case "$warm_code" in
  2*) green "warm-up OK (HTTP $warm_code)" ;;
  *)  red "warm-up failed (HTTP $warm_code) — aborting before we generate noise"; exit 1 ;;
esac

# --- helpers --------------------------------------------------------------

snapshot_stats() {
  docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" \
    | grep -E "gateway-(kong|n8n|postgres|redis|redpanda-0|alertmanager|grafana|prometheus)" \
    | awk '{printf "  %-28s cpu=%-8s mem=%s %s\n", $1, $2, $3, $4}'
}

snapshot_kafka_lag() {
  docker exec gateway-redpanda-0 rpk group list --brokers redpanda-0:29092 2>/dev/null \
    | tail -n +2 \
    | while read -r group _; do
        [ -z "$group" ] && continue
        lag=$(docker exec gateway-redpanda-0 rpk group describe "$group" --brokers redpanda-0:29092 2>/dev/null \
              | awk '/samsara-events/ {sum+=$6} END {print sum+0}')
        printf "  %-40s lag=%s\n" "$group" "$lag"
      done
}

run_stage() {
  local rps="$1"
  local total=$((rps * DURATION))
  local tmp; tmp=$(mktemp)

  info "stage: target ${rps} req/s, total ${total} requests, c=${CONCURRENCY}"

  if [ "$LOADER" = "hey" ]; then
    hey -n "$total" -c "$CONCURRENCY" -q "$rps" -t 10 \
      -m POST \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${SMOKE_API_KEY}" \
      -H "X-Samsara-Signature: ${SIG}" \
      -d "$BODY" \
      --disable-keepalive=false \
      "$ENDPOINT" 2>&1 | tee "$tmp" >/dev/null
  else
    oha --no-tui -q "$rps" -c "$CONCURRENCY" -z "${DURATION}s" \
      -m POST \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${SMOKE_API_KEY}" \
      -H "X-Samsara-Signature: ${SIG}" \
      -d "$BODY" \
      "$ENDPOINT" 2>&1 | tee "$tmp" >/dev/null
  fi

  echo "$tmp"
}

parse_hey() {
  local file="$1"
  awk '
    /Requests\/sec:/ {printf "throughput_rps=%s\n", $2}
    /Total:/ && !total  {total=$2;   printf "total_s=%s\n", $2}
    /Average:/          {printf "avg_s=%s\n", $2}
    /Slowest:/          {printf "slowest_s=%s\n", $2}
    /Fastest:/          {printf "fastest_s=%s\n", $2}
    /50% in/            {printf "p50_s=%s\n", $3}
    /90% in/            {printf "p90_s=%s\n", $3}
    /95% in/            {printf "p95_s=%s\n", $3}
    /99% in/            {printf "p99_s=%s\n", $3}
    /\[200\]/           {printf "http_200=%s\n", $2}
    /\[202\]/           {printf "http_202=%s\n", $2}
    /\[401\]/           {printf "http_401=%s\n", $2}
    /\[403\]/           {printf "http_403=%s\n", $2}
    /\[429\]/           {printf "http_429=%s\n", $2}
    /\[500\]/           {printf "http_500=%s\n", $2}
    /\[503\]/           {printf "http_503=%s\n", $2}
  ' "$file"
}

# --- report header --------------------------------------------------------

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<EOF
# Gateway load test — ${TS}

**Target:** \`${ENDPOINT}\`
**Loader:** ${LOADER}
**Stages:** ${STAGES} req/s × ${DURATION}s each, c=${CONCURRENCY}
**Host:** $(uname -s) $(uname -m), $(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo ?) CPUs
**Stack:** Kong (DB-less) + n8n (regular mode, 1 process) + Redpanda (single-node) + Postgres + Redis

## Baseline (no load)

\`\`\`
$(snapshot_stats)
\`\`\`

EOF

# --- run each stage -------------------------------------------------------

echo "## Per-stage results" >> "$OUT"
echo >> "$OUT"
echo "| Target RPS | Achieved | p50 | p95 | p99 | 2xx | 401 | 429 | 5xx |" >> "$OUT"
echo "|-----------:|---------:|----:|----:|----:|----:|----:|----:|----:|" >> "$OUT"

FIRST_STAGE=1
for rps in $STAGES; do
  [ "$FIRST_STAGE" = 1 ] || sleep 5
  FIRST_STAGE=0

  tmp=$(run_stage "$rps")
  stats_file=$(mktemp); snapshot_stats > "$stats_file"
  lag_file=$(mktemp);   snapshot_kafka_lag > "$lag_file"

  eval "$(parse_hey "$tmp")"

  two_xx=$(( ${http_200:-0} + ${http_202:-0} ))
  five_xx=$(( ${http_500:-0} + ${http_503:-0} ))

  printf "| %d | %s | %s | %s | %s | %d | %s | %s | %d |\n" \
    "$rps" \
    "${throughput_rps:-?}" \
    "${p50_s:-?}" "${p95_s:-?}" "${p99_s:-?}" \
    "$two_xx" "${http_401:-0}" "${http_429:-0}" "$five_xx" >> "$OUT"

  {
    echo
    echo "### Stage: target ${rps} req/s"
    echo
    echo "**Loader output (summary):**"
    echo '```'
    grep -E "Summary:|Total:|Slowest:|Fastest:|Average:|Requests/sec:|Latency distribution:|%% in|Status code distribution:|\\[[0-9]{3}\\]" "$tmp" || cat "$tmp" | head -40
    echo '```'
    echo
    echo "**Container stats (post-stage):**"
    echo '```'
    cat "$stats_file"
    echo '```'
    echo
    echo "**Kafka consumer lag (post-stage):**"
    echo '```'
    cat "$lag_file"
    echo '```'
  } >> "${OUT}.appendix"

  rm -f "$tmp" "$stats_file" "$lag_file"

  info "  stage done: ${throughput_rps:-?} rps achieved, 2xx=${two_xx}, 429=${http_429:-0}, 5xx=${five_xx}"

  if [ "$five_xx" -gt $((two_xx / 10)) ] && [ "$five_xx" -gt 100 ]; then
    yellow "  5xx rate exceeded 10% — stopping walk-up early"
    break
  fi
done

# --- report conclusion ----------------------------------------------------

if [ -f "${OUT}.appendix" ]; then
  echo >> "$OUT"
  echo "## Appendix — per-stage detail" >> "$OUT"
  cat "${OUT}.appendix" >> "$OUT"
  rm -f "${OUT}.appendix"
fi

cat >> "$OUT" <<'EOF'

## How to read this

- **Achieved rps < Target rps**: loader couldn't keep up OR server throttled. Look at the 429 column — if it's high, the loadtest overlay didn't fully take effect.
- **p95 climbing faster than achieved rps**: queueing. n8n's single process is gating. Switch to queue mode + scale `--scale n8n-worker=N`.
- **5xx cluster**: `docker compose logs gateway-kong gateway-n8n` and check the Kafka lag line — if lag grew, producers are timing out. Raise Redpanda memory or reduce n8n Kafka node `maxInFlightRequests`.

## Known bottleneck candidates (watch in order)

1. **n8n webhook handler**: single Node.js process; synchronous through Kafka produce.
2. **Kong Lua workers**: default 'auto' = N CPUs. On 2-CPU runners, saturates around 1-2k rps with the HMAC + key-auth chain.
3. **Redis (rate-limit counter + n8n queue)**: each request hits Redis.
4. **Redpanda produce acks**: `acks=all` adds latency under load; batching helps.
EOF

green "✅ Report: ${OUT}"
echo
info "Quick view: less ${OUT}"

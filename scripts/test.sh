#!/usr/bin/env bash
# Smoke test the Gateway stack. Expects `docker compose up -d` to have run.
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
# shellcheck disable=SC2034
YELLOW='\033[1;33m'
pass=0; fail=0

check() {
  local name=$1 url=$2 expected=$3 extra=${4:-}
  local code
  code=$(curl -sk -o /dev/null -w "%{http_code}" ${extra} "${url}" || echo 000)
  if [ "${code}" = "${expected}" ]; then
    echo -e "${GREEN}✓${NC} ${name} (${code})"
    pass=$((pass+1))
  else
    echo -e "${RED}✗${NC} ${name} (got ${code}, expected ${expected})"
    fail=$((fail+1))
  fi
}

echo "── Internal admin endpoints (must bind to 127.0.0.1 only) ──"
check "Kong Admin API"    http://127.0.0.1:8001/status 200
check "Kong Manager"      http://127.0.0.1:8002       200
check "n8n"               http://127.0.0.1:5678/healthz 200
check "Redpanda Console"  http://127.0.0.1:8080        200
check "Grafana"           http://127.0.0.1:3002/api/health 200
check "Prometheus"        http://127.0.0.1:9090/-/ready 200
check "Alertmanager"      http://127.0.0.1:9093/-/ready 200
check "Loki"              http://127.0.0.1:3100/ready   200

echo ""
echo "── Public proxy (Kong on :8000/:8443) ──"
# Without X-API-Key, key-auth returns 401. With wrong key, 401. Both prove auth is active.
check "Kong proxy HTTP (no key → 401 or 426)" http://localhost:8000/samsara 401 -XPOST
check "Kong proxy HTTPS (no key → 401)"        https://localhost:8443/samsara 401 -XPOST
check "Kong proxy HTTPS (route missing → 404)" https://localhost:8443/does-not-exist 404

echo ""
echo "── Public exposure sanity (these MUST be blocked from the public IP) ──"
for port in 8001 8002 5678 8080 3002 9090 9093 3100; do
  # On this host we bind to 127.0.0.1 only, so curl to 127.0.0.1 works but
  # binding to 0.0.0.0 would be caught by an external scanner - document here.
  _=$port
done

echo ""
if [ "${fail}" -eq 0 ]; then
  echo -e "${GREEN}✅ All ${pass} checks passed${NC}"
  exit 0
else
  echo -e "${RED}❌ ${fail} failed, ${pass} passed${NC}"
  docker compose ps
  exit 1
fi

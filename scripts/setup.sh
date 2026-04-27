#!/usr/bin/env bash
# First-time setup: verify prereqs, generate TLS cert, start stack.
#
# Flags:
#   --single-node   Set Redpanda replication to 1 (single-broker box / testbed)
#   --admin         Also start the admin-ui profile after the core stack
set -euo pipefail

SINGLE_NODE=0
START_ADMIN=0
for arg in "$@"; do
  case "$arg" in
    --single-node) SINGLE_NODE=1 ;;
    --admin)       START_ADMIN=1 ;;
  esac
done

echo "🚀 Gateway Setup"
echo ""

command -v docker >/dev/null || { echo "❌ Docker not installed"; exit 1; }
docker info >/dev/null 2>&1   || { echo "❌ Docker daemon not running"; exit 1; }
echo "✅ Docker: $(docker --version)"

# .env
if [ ! -f .env ]; then
  echo "📝 .env not found; copying from .env.example"
  cp .env.example .env
  echo "   ⚠️  Edit .env and replace CHANGE_ME values before going to prod."
fi

# Single-node: ensure REDPANDA_DEFAULT_REPLICAS=1 in .env
if [ "$SINGLE_NODE" -eq 1 ]; then
  grep -q "REDPANDA_DEFAULT_REPLICAS" .env \
    && sed -i.bak 's/^#\?REDPANDA_DEFAULT_REPLICAS=.*/REDPANDA_DEFAULT_REPLICAS=1/' .env \
    || echo "REDPANDA_DEFAULT_REPLICAS=1" >> .env
  echo "✅ Single-node mode: REDPANDA_DEFAULT_REPLICAS=1"
fi

# TLS cert for local/remote dev
if [ ! -f config/kong/certs/server.crt ]; then
  echo "🔐 Generating self-signed TLS cert (1 year)..."
  mkdir -p config/kong/certs
  CN="${TLS_CN:-localhost}"
  openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
    -keyout config/kong/certs/server.key \
    -out    config/kong/certs/server.crt \
    -subj "/CN=${CN}" 2>/dev/null
  chmod 600 config/kong/certs/server.key
  echo "   ⚠️  Replace with a real cert (ACM/Let's Encrypt) before production."
fi

echo ""
echo "🔧 Starting core services..."
docker compose up -d --wait --wait-timeout 300

echo ""
echo "📦 Creating Redpanda topics..."
REPLICAS="${REDPANDA_DEFAULT_REPLICAS:-3}" bash scripts/create-topics.sh || true

echo ""
echo "🔗 Bootstrapping n8n..."
bash scripts/n8n-bootstrap.sh || true

if [ "$START_ADMIN" -eq 1 ]; then
  echo ""
  echo "🖥  Starting admin UI..."
  docker compose --profile admin up -d --build --wait --wait-timeout 120
fi

echo ""
echo "📊 Service status:"
docker compose ps

echo ""
echo "🎉 Done."
echo ""
echo "Public proxy (Kong):"
echo "  https://localhost:8443/{samsara,netsuite,tai,wms,unigroup}   (X-API-Key required)"
echo ""
echo "Admin UIs (127.0.0.1 only — use SSH tunnel for remote access):"
echo "  Gateway Admin   http://127.0.0.1:7070   (start with --admin or --profile admin)"
echo "  n8n             http://127.0.0.1:5678"
echo "  Redpanda        http://127.0.0.1:8080"
echo "  Grafana         http://127.0.0.1:3002"
echo "  Prometheus      http://127.0.0.1:9090"
echo "  Alertmanager    http://127.0.0.1:9093"
echo "  Kong Admin      http://127.0.0.1:8001"
echo ""
echo "Run ./scripts/test.sh to smoke-test."

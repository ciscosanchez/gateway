#!/usr/bin/env bash
# First-time setup: verify prereqs, generate TLS cert for local dev, start stack.
set -euo pipefail

echo "🚀 Gateway Setup"
echo ""

command -v docker >/dev/null || { echo "❌ Docker not installed"; exit 1; }
docker info >/dev/null 2>&1 || { echo "❌ Docker daemon not running"; exit 1; }
echo "✅ Docker: $(docker --version)"

# .env
if [ ! -f .env ]; then
  echo "📝 .env not found; copying from .env.example"
  cp .env.example .env
  echo "   ⚠️  Edit .env and replace CHANGE_ME values before going to prod."
fi

# TLS cert for local dev
if [ ! -f config/kong/certs/server.crt ]; then
  echo "🔐 Generating self-signed TLS cert for local dev (1 year)..."
  mkdir -p config/kong/certs
  openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
    -keyout config/kong/certs/server.key \
    -out    config/kong/certs/server.crt \
    -subj "/CN=localhost" 2>/dev/null
  chmod 600 config/kong/certs/server.key
  echo "   ⚠️  Replace with a real cert (ACM/Let's Encrypt) before production."
fi

echo ""
echo "🔧 Starting services..."
docker compose up -d --wait --wait-timeout 180

echo ""
echo "📦 Creating Redpanda topics..."
./scripts/create-topics.sh || true

echo ""
echo "📊 Service status:"
docker compose ps

echo ""
echo "🎉 Done."
echo ""
echo "Public proxy (Kong):"
echo "  https://localhost:8443/{samsara,netsuite,wms,unigroup}   (requires X-API-Key)"
echo ""
echo "Admin UIs (127.0.0.1 only; tunnel via SSH if remote):"
echo "  Kong Admin      http://127.0.0.1:8001"
echo "  Kong Manager    http://127.0.0.1:8002"
echo "  n8n             http://127.0.0.1:5678"
echo "  Redpanda        http://127.0.0.1:8080"
echo "  Grafana         http://127.0.0.1:3002"
echo "  Prometheus      http://127.0.0.1:9090"
echo "  Alertmanager    http://127.0.0.1:9093"
echo ""
echo "Run ./scripts/test.sh to smoke-test."

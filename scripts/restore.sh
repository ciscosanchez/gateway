#!/usr/bin/env bash
# Restore encrypted/plaintext Gateway backups.
# Works with both .sql.gz and .sql.gz.age variants produced by backup.sh.
set -euo pipefail

# shellcheck disable=SC1091
[ -f .env ] && source .env

if [ $# -lt 1 ]; then
  echo "Usage: $0 <backup_directory>"
  echo "Example: $0 ./backups/20260417_120000"
  exit 1
fi

BACKUP_DIR="$1"
[ -d "${BACKUP_DIR}" ] || { echo "❌ ${BACKUP_DIR} not found"; exit 1; }

echo "🔄 Gateway Restore from: ${BACKUP_DIR}"
echo ""
read -r -p "⚠️  This OVERWRITES current data. Type 'yes' to continue: " confirm
[ "${confirm}" = "yes" ] || { echo "cancelled"; exit 0; }

decrypt_if_needed() {
  local path="$1"
  if [ -f "${path}.age" ]; then
    [ -n "${BACKUP_AGE_IDENTITY_FILE:-}" ] || {
      echo "❌ Encrypted backup found but BACKUP_AGE_IDENTITY_FILE not set"; exit 1; }
    age -d -i "${BACKUP_AGE_IDENTITY_FILE}" -o "${path}" "${path}.age"
  fi
}

echo "🛑 Stopping workers that write state..."
docker compose stop n8n n8n-worker kong || true

# --- Postgres (gateway / n8n) ---
sql="${BACKUP_DIR}/postgres-gateway.sql.gz"
decrypt_if_needed "${sql}"
if [ -f "${sql}" ]; then
  echo "📦 Restoring Postgres (${POSTGRES_DB})..."
  docker exec -i gateway-postgres psql -U "${POSTGRES_USER}" -c \
    "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
  docker exec -i gateway-postgres psql -U "${POSTGRES_USER}" -c \
    "CREATE DATABASE ${POSTGRES_DB};"
  gunzip -c "${sql}" | docker exec -i gateway-postgres \
    psql -U "${POSTGRES_USER}" "${POSTGRES_DB}"
  echo "✅ Postgres restored"
else
  echo "⚠️  postgres-gateway.sql.gz not present; skipping"
fi

# --- Kong (declarative) ---
if [ -f "${BACKUP_DIR}/kong.yml" ]; then
  echo "📦 Restoring Kong declarative config..."
  cp "${BACKUP_DIR}/kong.yml" kong/kong.yml
  echo "✅ kong/kong.yml restored (Kong will reload on restart)"
fi

# --- Redpanda topic list - recreate structure only ---
topics="${BACKUP_DIR}/redpanda-topics.txt"
decrypt_if_needed "${topics}"
if [ -f "${topics}" ]; then
  echo "📦 Redpanda topics present in snapshot (data NOT restored):"
  cat "${topics}" | sed 's/^/  /'
  echo "   -> run ./scripts/create-topics.sh to re-create if needed"
fi

echo ""
echo "🚀 Restarting services..."
docker compose up -d
sleep 15
docker compose ps

echo ""
echo "✅ Restore complete."
echo "Next:"
echo "  1. ./scripts/test.sh"
echo "  2. Verify n8n workflows at http://127.0.0.1:5678"
echo "  3. Verify Kong at curl -sk https://localhost:8443/"

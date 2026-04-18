#!/usr/bin/env bash
# Encrypted backup: Postgres (n8n/app state) + n8n workflows export + Redpanda
# topic metadata. Encrypts with `age` using BACKUP_AGE_RECIPIENT from .env.
# Retention enforced by BACKUP_RETENTION_DAYS (default 30).
set -euo pipefail

# shellcheck disable=SC1091
[ -f .env ] && source .env

RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="./backups/${TS}"
mkdir -p "${OUT_DIR}"

echo "🗄️  Gateway backup → ${OUT_DIR}"

have_age=0
ALLOW_PLAINTEXT="${ALLOW_PLAINTEXT_BACKUP:-0}"
if command -v age >/dev/null 2>&1 && [ -n "${RECIPIENT}" ] && [[ "${RECIPIENT}" =~ ^age1 ]]; then
  have_age=1
elif [ "${ALLOW_PLAINTEXT}" = "1" ]; then
  echo "⚠️  ALLOW_PLAINTEXT_BACKUP=1: proceeding WITHOUT encryption."
else
  echo "❌  age not configured. Set BACKUP_AGE_RECIPIENT in .env to an age1... pubkey," >&2
  echo "    or set ALLOW_PLAINTEXT_BACKUP=1 to force an unencrypted dump (not recommended)." >&2
  exit 1
fi

encrypt_inline() {
  local src="$1"
  if [ "${have_age}" = "1" ]; then
    age -r "${RECIPIENT}" -o "${src}.age" "${src}" && rm -f "${src}"
    echo "  encrypted → $(basename "${src}.age")"
  fi
}

echo "📦 pg_dump (gateway/n8n)…"
docker exec gateway-postgres pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  | gzip -9 > "${OUT_DIR}/postgres-gateway.sql.gz"
encrypt_inline "${OUT_DIR}/postgres-gateway.sql.gz"

echo "📦 Redpanda topic metadata…"
docker exec gateway-redpanda-0 rpk topic list --brokers redpanda-0:29092 \
  > "${OUT_DIR}/redpanda-topics.txt" 2>/dev/null || true
docker exec gateway-redpanda-0 rpk cluster config export \
  > "${OUT_DIR}/redpanda-cluster-config.yml" 2>/dev/null || true
encrypt_inline "${OUT_DIR}/redpanda-topics.txt"
encrypt_inline "${OUT_DIR}/redpanda-cluster-config.yml"

echo "📦 n8n workflows (via REST)…"
# Requires N8N_API_KEY or basic auth; best-effort
mkdir -p "${OUT_DIR}/workflows"
if curl -fsS -u "${N8N_BASIC_AUTH_USER}:${N8N_BASIC_AUTH_PASSWORD}" \
    "http://127.0.0.1:5678/rest/workflows" \
    > "${OUT_DIR}/workflows/all.json" 2>/dev/null; then
  encrypt_inline "${OUT_DIR}/workflows/all.json"
else
  echo "  (n8n API not reachable, skipping)"
fi

echo "📦 Kong declarative config snapshot…"
cp kong/kong.yml "${OUT_DIR}/kong.yml" || true

cat > "${OUT_DIR}/manifest.txt" <<EOF
Gateway Backup Manifest
=======================
Timestamp     : $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Host          : $(hostname)
Encrypted     : $([ "${have_age}" = "1" ] && echo yes || echo no)
Recipient     : ${RECIPIENT:-<none>}
Retention     : ${RETENTION_DAYS} days
Contents:
  - postgres-gateway.sql.gz[.age]
  - redpanda-topics.txt[.age]
  - redpanda-cluster-config.yml[.age]
  - workflows/all.json[.age]
  - kong.yml
EOF

echo ""
echo "✅ Backup complete → ${OUT_DIR}"
du -sh "${OUT_DIR}"

# Retention: delete any backup dir older than N days
echo ""
echo "🧹 Pruning backups older than ${RETENTION_DAYS}d…"
find ./backups -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" \
  -print -exec rm -rf {} +

echo ""
echo "Reminder: ship ${OUT_DIR} off-host (S3 w/ SSE-KMS, or equivalent)."

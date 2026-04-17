#!/bin/bash

# Backup Script for Gateway Services
# Backs up PostgreSQL databases and Redpanda topics

set -e

BACKUP_DIR="./backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "🗄️  Gateway Backup Started"
echo "Backup location: $BACKUP_DIR"
echo ""

# Backup PostgreSQL (n8n database)
echo "📦 Backing up PostgreSQL (n8n)..."
docker exec gateway-postgres pg_dump -U gateway gateway > "$BACKUP_DIR/postgres-gateway.sql"
echo "✅ PostgreSQL backup complete: postgres-gateway.sql"

# Backup Kong database
echo "📦 Backing up PostgreSQL (Kong)..."
docker exec gateway-kong-db pg_dump -U kong kong > "$BACKUP_DIR/postgres-kong.sql"
echo "✅ Kong database backup complete: postgres-kong.sql"

# Export Redpanda topic list
echo "📦 Exporting Redpanda topic metadata..."
docker exec gateway-redpanda rpk topic list > "$BACKUP_DIR/redpanda-topics.txt"
echo "✅ Topic list exported: redpanda-topics.txt"

# Export n8n workflows (if available via API)
echo "📦 Exporting n8n workflows..."
if command -v curl &> /dev/null; then
  mkdir -p "$BACKUP_DIR/workflows"
  # Note: This requires n8n API to be accessible
  # You may need to adjust the URL and add authentication
  echo "  (Manual step: Export workflows from n8n UI to $BACKUP_DIR/workflows/)"
fi

# Create backup manifest
cat > "$BACKUP_DIR/manifest.txt" << EOF
Gateway Backup Manifest
=======================
Date: $(date)
Components:
- PostgreSQL (gateway database): postgres-gateway.sql
- PostgreSQL (Kong database): postgres-kong.sql
- Redpanda topics metadata: redpanda-topics.txt
- n8n workflows: workflows/ (manual export required)

To restore:
./scripts/restore.sh $BACKUP_DIR
EOF

echo ""
echo "✅ Backup complete!"
echo ""
echo "Backup contents:"
ls -lh "$BACKUP_DIR"
echo ""
echo "💾 Total size: $(du -sh $BACKUP_DIR | cut -f1)"
echo ""
echo "To restore this backup:"
echo "  ./scripts/restore.sh $BACKUP_DIR"
echo ""

# Compress backup (optional)
if command -v tar &> /dev/null; then
  BACKUP_ARCHIVE="${BACKUP_DIR}.tar.gz"
  echo "🗜️  Compressing backup..."
  tar -czf "$BACKUP_ARCHIVE" -C "./backups" "$(basename $BACKUP_DIR)"
  echo "✅ Compressed: $BACKUP_ARCHIVE"
  echo "💾 Archive size: $(du -sh $BACKUP_ARCHIVE | cut -f1)"
fi

echo ""
echo "⚠️  IMPORTANT: Store backups in a secure, off-site location!"
echo "   Recommended: AWS S3, Google Cloud Storage, or encrypted external drive"

#!/bin/bash

# Restore Script for Gateway Services
# Restores PostgreSQL databases from backup

set -e

if [ -z "$1" ]; then
  echo "Usage: ./scripts/restore.sh <backup_directory>"
  echo ""
  echo "Example:"
  echo "  ./scripts/restore.sh ./backups/20260417_120000"
  exit 1
fi

BACKUP_DIR="$1"

if [ ! -d "$BACKUP_DIR" ]; then
  echo "❌ Error: Backup directory not found: $BACKUP_DIR"
  exit 1
fi

echo "🔄 Gateway Restore Started"
echo "Restoring from: $BACKUP_DIR"
echo ""

# Confirmation
read -p "⚠️  This will OVERWRITE current data. Are you sure? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
  echo "Restore cancelled."
  exit 0
fi

echo ""

# Stop services that write to databases
echo "🛑 Stopping services..."
docker compose stop n8n kong
echo "✅ Services stopped"
echo ""

# Restore PostgreSQL (n8n database)
if [ -f "$BACKUP_DIR/postgres-gateway.sql" ]; then
  echo "📦 Restoring PostgreSQL (n8n)..."
  
  # Drop and recreate database
  docker exec gateway-postgres psql -U gateway -c "DROP DATABASE IF EXISTS gateway;"
  docker exec gateway-postgres psql -U gateway -c "CREATE DATABASE gateway;"
  
  # Restore from backup
  docker exec -i gateway-postgres psql -U gateway gateway < "$BACKUP_DIR/postgres-gateway.sql"
  echo "✅ PostgreSQL (n8n) restored"
else
  echo "⚠️  Skipping PostgreSQL (n8n): backup file not found"
fi

echo ""

# Restore Kong database
if [ -f "$BACKUP_DIR/postgres-kong.sql" ]; then
  echo "📦 Restoring PostgreSQL (Kong)..."
  
  # Drop and recreate database
  docker exec gateway-kong-db psql -U kong -c "DROP DATABASE IF EXISTS kong;"
  docker exec gateway-kong-db psql -U kong -c "CREATE DATABASE kong;"
  
  # Restore from backup
  docker exec -i gateway-kong-db psql -U kong kong < "$BACKUP_DIR/postgres-kong.sql"
  echo "✅ PostgreSQL (Kong) restored"
else
  echo "⚠️  Skipping PostgreSQL (Kong): backup file not found"
fi

echo ""

# Restore Redpanda topics (metadata only)
if [ -f "$BACKUP_DIR/redpanda-topics.txt" ]; then
  echo "📦 Recreating Redpanda topics..."
  while read -r line; do
    # Skip header lines
    if [[ $line == NAME* ]] || [[ $line == ----* ]]; then
      continue
    fi
    
    TOPIC=$(echo $line | awk '{print $1}')
    if [ -n "$TOPIC" ]; then
      echo "  Creating topic: $TOPIC"
      docker exec gateway-redpanda rpk topic create "$TOPIC" --partitions 3 --replicas 1 || true
    fi
  done < "$BACKUP_DIR/redpanda-topics.txt"
  echo "✅ Redpanda topics recreated"
else
  echo "⚠️  Skipping Redpanda topics: backup file not found"
fi

echo ""

# Restart services
echo "🚀 Restarting services..."
docker compose up -d
echo "✅ Services restarted"

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 10
docker compose ps

echo ""
echo "✅ Restore complete!"
echo ""
echo "Next steps:"
echo "  1. Verify n8n workflows: http://localhost:5678"
echo "  2. Verify Kong routes: curl http://localhost:8001/routes"
echo "  3. Check Redpanda topics: docker exec -it gateway-redpanda rpk topic list"
echo "  4. Run smoke tests: ./scripts/test.sh"

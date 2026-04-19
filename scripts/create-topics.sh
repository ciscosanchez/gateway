#!/usr/bin/env bash
# Creates Redpanda topics for the Gateway platform.
# Runs against the 3-node cluster. Safe to re-run (idempotent).
set -euo pipefail

BROKER="${BROKER:-redpanda-0:29092}"
CONTAINER="${CONTAINER:-gateway-redpanda-0}"

echo "📨 Creating Redpanda topics on ${BROKER}..."

# topic_name partitions replication retention_ms compression
topics=(
  # high volume per-vehicle events; key by vehicle_id for ordering
  "samsara-events        24 3 604800000  zstd"        # 7d
  # business events
  "orders                12 3 2592000000 zstd"        # 30d
  "inventory              6 3 604800000  zstd"        # 7d
  # Unigroup Converge outbound: messages other workflows publish when they
  # want us to call Unigroup's GraphQL/documents API. Processed by the
  # unigroup-outbound workflow.
  "unigroup-out           6 3 2592000000 zstd"        # 30d
  # Unigroup outcomes + pulled shipment updates from polling workflows.
  "unigroup-in            6 3 604800000  zstd"        # 7d
  # Legacy: any remaining true-EDI integrations. Will be removed once all
  # flows are migrated off EDI.
  "edi-outbound           6 3 2592000000 zstd"        # 30d
  "netsuite-updates      12 3 604800000  zstd"        # 7d
  "wms-events             6 3 604800000  zstd"        # 7d
  # Dead-letter queue - low volume, long retention for forensic analysis
  "errors-dlq             3 3 7776000000 zstd"        # 90d
)

for row in "${topics[@]}"; do
  # shellcheck disable=SC2086
  set -- $row
  name=$1; partitions=$2; replication=$3; retention=$4; compression=$5

  echo ""
  echo "→ ${name}  (p=${partitions} r=${replication} retention=${retention}ms compression=${compression})"

  docker exec "${CONTAINER}" rpk topic create "${name}" \
    --brokers "${BROKER}" \
    --partitions "${partitions}" \
    --replicas "${replication}" \
    -c "retention.ms=${retention}" \
    -c "compression.type=${compression}" \
    -c "min.insync.replicas=2" \
    -c "cleanup.policy=delete" \
    -c "max.message.bytes=1048576" \
    2>&1 | grep -vE "already exists|WARN" || true
done

echo ""
echo "✅ Topic list:"
docker exec "${CONTAINER}" rpk topic list --brokers "${BROKER}"

echo ""
echo "Ensure producers use: acks=all, enable.idempotence=true, max.in.flight=5"

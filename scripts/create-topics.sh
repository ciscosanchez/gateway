#!/usr/bin/env bash
# Creates Redpanda topics for the Gateway platform.
# Safe to re-run (idempotent — existing topics are skipped).
#
# Single-node override:
#   REPLICAS=1 bash scripts/create-topics.sh
# The default (3) is correct for a multi-node cluster.
set -euo pipefail

BROKER="${BROKER:-redpanda-0:29092}"
CONTAINER="${CONTAINER:-gateway-redpanda-0}"
# Allow single-node override: REPLICAS=1 ./create-topics.sh
REPLICAS="${REPLICAS:-3}"
# min.insync.replicas must be <= REPLICAS; use REPLICAS-1 (floor 1)
MIN_ISR=$(( REPLICAS > 1 ? REPLICAS - 1 : 1 ))

echo "📨 Creating Redpanda topics on ${BROKER} (replicas=${REPLICAS}, min_isr=${MIN_ISR})..."

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
  "wms-out                6 3 2592000000 zstd"        # 30d - outbound to WMS
  "wms-updates            6 3 604800000  zstd"        # 7d  - WMS responses
  "dispatch-out           6 3 2592000000 zstd"        # 30d - outbound to Dispatch
  "dispatch-updates       6 3 604800000  zstd"        # 7d  - Dispatch responses
  # Tai TMS integration
  "tai-bills              6 3 2592000000 zstd"        # 30d accounts-payable events
  "tai-invoices           6 3 2592000000 zstd"        # 30d accounts-receivable events
  "tai-shipments         12 3 604800000  zstd"        # 7d  high-volume shipment events
  "tai-customers          3 3 2592000000 zstd"        # 30d customer entity changes
  "tai-carriers           3 3 2592000000 zstd"        # 30d carrier entity changes
  "tai-out                6 3 2592000000 zstd"        # 30d outbound queue → Tai API
  "tai-updates            6 3 604800000  zstd"        # 7d  Tai API response confirmations
  # Dead-letter queue - low volume, long retention for forensic analysis
  "errors-dlq             3 3 7776000000 zstd"        # 90d
)

for row in "${topics[@]}"; do
  # shellcheck disable=SC2086
  set -- $row
  name=$1; partitions=$2; _default_replication=$3; retention=$4; compression=$5
  # Use the REPLICAS override (single-node) or the per-topic default
  effective_replication="${REPLICAS}"

  echo ""
  echo "→ ${name}  (p=${partitions} r=${effective_replication} isr=${MIN_ISR} retention=${retention}ms compression=${compression})"

  docker exec "${CONTAINER}" rpk topic create "${name}" \
    --brokers "${BROKER}" \
    --partitions "${partitions}" \
    --replicas "${effective_replication}" \
    -c "retention.ms=${retention}" \
    -c "compression.type=${compression}" \
    -c "min.insync.replicas=${MIN_ISR}" \
    -c "cleanup.policy=delete" \
    -c "max.message.bytes=1048576" \
    2>&1 | grep -vE "already exists|WARN" || true
done

echo ""
echo "✅ Topic list:"
docker exec "${CONTAINER}" rpk topic list --brokers "${BROKER}"

echo ""
echo "Ensure producers use: acks=all, enable.idempotence=true, max.in.flight=5"

#!/bin/bash

echo "📨 Creating Redpanda topics..."
echo ""

CONTAINER="gateway-redpanda"

# Function to create topic
create_topic() {
    local TOPIC_NAME=$1
    local PARTITIONS=${2:-3}
    local REPLICATION=${3:-1}
    
    echo "Creating topic: $TOPIC_NAME (partitions: $PARTITIONS, replication: $REPLICATION)"
    
    docker exec -it $CONTAINER rpk topic create $TOPIC_NAME \
        --partitions $PARTITIONS \
        --replicas $REPLICATION 2>&1 | grep -v "WARN"
    
    echo ""
}

# Create topics for event streams
create_topic "samsara-events" 3 1
create_topic "orders" 3 1
create_topic "inventory" 3 1
create_topic "edi-outbound" 3 1
create_topic "netsuite-updates" 3 1
create_topic "wms-events" 3 1
create_topic "errors-dlq" 1 1

echo "🎉 Topics created!"
echo ""
echo "List all topics:"
docker exec -it $CONTAINER rpk topic list

echo ""
echo "View topics in Redpanda Console: http://localhost:8080"

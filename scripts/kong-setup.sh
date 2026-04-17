#!/bin/bash

echo "🔧 Setting up Kong routes and services..."
echo ""

KONG_ADMIN="http://localhost:8001"

# Function to create service and route
create_service_route() {
    local SERVICE_NAME=$1
    local UPSTREAM_URL=$2
    local ROUTE_PATH=$3
    
    echo "Creating service: $SERVICE_NAME"
    
    curl -s -X POST "$KONG_ADMIN/services" \
        --data "name=$SERVICE_NAME" \
        --data "url=$UPSTREAM_URL" > /dev/null
    
    echo "Creating route: $ROUTE_PATH"
    
    curl -s -X POST "$KONG_ADMIN/services/$SERVICE_NAME/routes" \
        --data "paths[]=$ROUTE_PATH" \
        --data "strip_path=false" > /dev/null
    
    echo "✅ $SERVICE_NAME configured"
    echo ""
}

# Create services for external systems
create_service_route "samsara-service" "http://n8n:5678/webhook/samsara" "/samsara"
create_service_route "netsuite-service" "http://n8n:5678/webhook/netsuite" "/netsuite"
create_service_route "wms-service" "http://n8n:5678/webhook/wms" "/wms"
create_service_route "unigroup-service" "http://n8n:5678/webhook/unigroup" "/unigroup"

echo "🎉 Kong routes configured!"
echo ""
echo "Test your routes:"
echo "  curl http://localhost:8000/samsara"
echo "  curl http://localhost:8000/netsuite"
echo "  curl http://localhost:8000/wms"
echo "  curl http://localhost:8000/unigroup"
echo ""
echo "View routes: curl http://localhost:8001/routes"
echo "Kong Manager UI: http://localhost:8002"

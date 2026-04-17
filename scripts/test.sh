#!/bin/bash

echo "🧪 Testing Gateway Stack..."
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to test endpoint
test_endpoint() {
    local NAME=$1
    local URL=$2
    local EXPECTED=$3
    
    echo -n "Testing $NAME... "
    
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "$URL" 2>&1)
    
    if [ "$RESPONSE" == "$EXPECTED" ]; then
        echo -e "${GREEN}✓ OK${NC} (HTTP $RESPONSE)"
    else
        echo -e "${RED}✗ FAILED${NC} (Expected HTTP $EXPECTED, got $RESPONSE)"
    fi
}

# Test services
echo "📡 Testing Service Endpoints:"
echo ""

test_endpoint "Kong Admin API" "http://localhost:8001" "200"
test_endpoint "Kong Manager" "http://localhost:8002" "200"
test_endpoint "Kong Proxy" "http://localhost:8000" "404"
test_endpoint "n8n" "http://localhost:5678" "200"
test_endpoint "Redpanda Console" "http://localhost:8080" "200"
test_endpoint "Grafana" "http://localhost:3002" "200"
test_endpoint "Prometheus" "http://localhost:9090/graph" "200"

echo ""
echo "🔍 Checking Docker Services:"
echo ""

docker compose ps

echo ""
echo "📊 Service URLs:"
echo ""
echo "  Kong Admin:        http://localhost:8001"
echo "  Kong Manager:      http://localhost:8002"
echo "  Kong Proxy:        http://localhost:8000"
echo "  n8n:              http://localhost:5678 (admin/admin)"
echo "  Redpanda Console: http://localhost:8080"
echo "  Grafana:          http://localhost:3002 (admin/admin)"
echo "  Prometheus:       http://localhost:9090"
echo ""

# Test Kong routes if they exist
echo "🛣️  Testing Kong Routes:"
echo ""

ROUTES=$(curl -s http://localhost:8001/routes 2>&1)

if echo "$ROUTES" | grep -q "samsara"; then
    echo -n "Testing Samsara Route... "
    ROUTE_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/samsara" 2>&1)
    if [ "$ROUTE_RESPONSE" == "426" ] || [ "$ROUTE_RESPONSE" == "404" ]; then
        echo -e "${GREEN}✓ OK${NC} (HTTP $ROUTE_RESPONSE - route exists)"
        echo "  Note: Route requires HTTPS (HTTP returns 426)"
    else
        echo -e "${YELLOW}⚠ Unexpected response${NC} (HTTP $ROUTE_RESPONSE)"
    fi
else
    echo -e "${YELLOW}⚠ Samsara route not configured${NC}"
    echo "  Run: ./scripts/kong-setup.sh"
fi

echo ""
echo "✅ Testing complete!"

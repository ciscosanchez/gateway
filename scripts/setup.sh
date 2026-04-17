#!/bin/bash

echo "🚀 Gateway Setup Script"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed."
    echo "   Please install Docker Desktop:"
    echo "   - Mac: https://www.docker.com/products/docker-desktop/"
    echo "   - Windows: https://www.docker.com/products/docker-desktop/"
    exit 1
fi

echo "✅ Docker is installed: $(docker --version)"

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "❌ Docker daemon is not running."
    echo "   Please start Docker Desktop and try again."
    exit 1
fi

echo "✅ Docker daemon is running"
echo ""

# Start services
echo "🔧 Starting services..."
docker compose up -d

echo ""
echo "⏳ Waiting for services to become healthy..."
sleep 10

# Check service health
echo ""
echo "📊 Service Status:"
docker compose ps

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Access your services:"
echo "  - Kong Admin:         http://localhost:8001"
echo "  - Kong Manager:       http://localhost:8002"
echo "  - n8n:               http://localhost:5678 (admin/admin)"
echo "  - Redpanda Console:  http://localhost:8080"
echo "  - Grafana:           http://localhost:3000 (admin/admin)"
echo ""
echo "Next steps:"
echo "  1. Configure Kong routes: ./scripts/kong-setup.sh"
echo "  2. Create Redpanda topics: ./scripts/create-topics.sh"
echo "  3. Build n8n workflows in the UI"
echo ""
echo "View logs: docker compose logs -f"
echo "Stop services: docker compose down"

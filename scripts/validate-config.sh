#!/bin/bash

# Configuration Validation Script
# Validates Gateway configuration before deployment

set -e

echo "🔍 Gateway Configuration Validation"
echo "===================================="
echo ""

ERRORS=0
WARNINGS=0

# Check if running as root
if [ "$EUID" -eq 0 ]; then
  echo "⚠️  WARNING: Running as root is not recommended"
  WARNINGS=$((WARNINGS+1))
fi

# Check Docker is running
echo "Checking Docker..."
if ! docker info > /dev/null 2>&1; then
  echo "❌ ERROR: Docker is not running"
  ERRORS=$((ERRORS+1))
else
  echo "✅ Docker is running"
fi

# Check docker-compose.yml exists
echo ""
echo "Checking docker-compose.yml..."
if [ ! -f "docker-compose.yml" ]; then
  echo "❌ ERROR: docker-compose.yml not found"
  ERRORS=$((ERRORS+1))
else
  echo "✅ docker-compose.yml found"
  
  # Validate YAML syntax
  if command -v docker compose config &> /dev/null; then
    if docker compose config > /dev/null 2>&1; then
      echo "✅ docker-compose.yml syntax is valid"
    else
      echo "❌ ERROR: docker-compose.yml has syntax errors"
      ERRORS=$((ERRORS+1))
    fi
  fi
fi

# Check .env file
echo ""
echo "Checking environment configuration..."
if [ ! -f ".env" ]; then
  echo "⚠️  WARNING: .env file not found (using .env.example?)"
  WARNINGS=$((WARNINGS+1))
else
  echo "✅ .env file exists"
  
  # Check for example/default values
  if grep -q "CHANGE_ME" .env; then
    echo "⚠️  WARNING: .env contains CHANGE_ME placeholders"
    WARNINGS=$((WARNINGS+1))
  fi
  
  if grep -q "your_.*_here" .env; then
    echo "⚠️  WARNING: .env contains placeholder values (your_*_here)"
    WARNINGS=$((WARNINGS+1))
  fi
  
  # Check for default dev passwords
  if grep -q "gateway_dev_password\|kong_dev_password" .env; then
    echo "⚠️  WARNING: Using default development passwords"
    WARNINGS=$((WARNINGS+1))
  fi
  
  # Check if secrets are strong
  if grep -q "admin" .env | grep -q "PASSWORD"; then
    echo "⚠️  WARNING: Weak passwords detected (contains 'admin')"
    WARNINGS=$((WARNINGS+1))
  fi
fi

# Check required directories exist
echo ""
echo "Checking directory structure..."
for dir in config workflows scripts docs; do
  if [ -d "$dir" ]; then
    echo "✅ $dir/ directory exists"
  else
    echo "⚠️  WARNING: $dir/ directory not found"
    WARNINGS=$((WARNINGS+1))
  fi
done

# Check required config files
echo ""
echo "Checking configuration files..."
CONFIG_FILES=(
  "config/prometheus/prometheus.yml"
  "config/grafana/provisioning/datasources/datasources.yml"
)

for file in "${CONFIG_FILES[@]}"; do
  if [ -f "$file" ]; then
    echo "✅ $file exists"
  else
    echo "⚠️  WARNING: $file not found"
    WARNINGS=$((WARNINGS+1))
  fi
done

# Check scripts are executable
echo ""
echo "Checking script permissions..."
SCRIPTS=(
  "scripts/kong-setup.sh"
  "scripts/test.sh"
  "scripts/backup.sh"
  "scripts/restore.sh"
  "scripts/secure-kong-routes.sh"
)

for script in "${SCRIPTS[@]}"; do
  if [ -f "$script" ]; then
    if [ -x "$script" ]; then
      echo "✅ $script is executable"
    else
      echo "⚠️  WARNING: $script is not executable"
      echo "   Fix: chmod +x $script"
      WARNINGS=$((WARNINGS+1))
    fi
  else
    echo "⚠️  WARNING: $script not found"
    WARNINGS=$((WARNINGS+1))
  fi
done

# Check for .env in git
echo ""
echo "Checking git configuration..."
if [ -d ".git" ]; then
  if git check-ignore .env > /dev/null 2>&1; then
    echo "✅ .env is gitignored"
  else
    echo "❌ ERROR: .env is NOT gitignored - SECURITY RISK!"
    ERRORS=$((ERRORS+1))
  fi
  
  # Check if secrets are committed
  if git log --all --full-history -- ".env" 2>/dev/null | grep -q "commit"; then
    echo "⚠️  WARNING: .env was committed to git history"
    echo "   Action required: Rotate all secrets and clean git history"
    WARNINGS=$((WARNINGS+1))
  fi
fi

# Check port conflicts
echo ""
echo "Checking for port conflicts..."
PORTS=(5432 8000 8001 8002 5678 8080 8081 8082 9090 9092 3002 3100)
for port in "${PORTS[@]}"; do
  if lsof -Pi :$port -sTCP:LISTEN -t > /dev/null 2>&1; then
    if docker ps --format '{{.Names}}' | grep -q "gateway-"; then
      # It's probably our services, that's OK
      :
    else
      echo "⚠️  WARNING: Port $port is already in use"
      WARNINGS=$((WARNINGS+1))
    fi
  fi
done

# Summary
echo ""
echo "===================================="
echo "Validation Summary"
echo "===================================="
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
  echo "✅ All checks passed!"
  exit 0
elif [ $ERRORS -eq 0 ]; then
  echo "⚠️  Passed with $WARNINGS warning(s)"
  exit 0
else
  echo "❌ Failed with $ERRORS error(s) and $WARNINGS warning(s)"
  exit 1
fi

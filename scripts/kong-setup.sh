#!/usr/bin/env bash
# DEPRECATED - Kong is now DB-less (declarative).
# Routes and plugins are defined in kong/kong.yml and loaded at startup.
# This script remains only to preserve the `scripts/kong-setup.sh` entry point
# used by old docs/CI. To apply changes, edit kong/kong.yml and restart Kong:
#
#   docker compose restart kong
#
# Or hot-reload via admin API (loopback only):
#   curl -s -X POST http://127.0.0.1:8001/config \
#        -F "config=@kong/kong.yml"
set -euo pipefail

echo "ℹ️  Kong is DB-less. Applying declarative config from kong/kong.yml..."
if curl -fsS -X POST http://127.0.0.1:8001/config \
        -F "config=@kong/kong.yml" >/dev/null; then
  echo "✅ Kong config reloaded."
else
  echo "❌ Could not reach Kong Admin on 127.0.0.1:8001. Is Kong running?"
  exit 1
fi

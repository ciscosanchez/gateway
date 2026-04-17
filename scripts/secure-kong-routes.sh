#!/usr/bin/env bash
# DEPRECATED - route hardening (auth, rate-limit, CORS, security headers,
# HMAC, IP allowlist, size limits) now lives in kong/kong.yml.
# Use scripts/kong-setup.sh to hot-apply after editing kong/kong.yml.
set -euo pipefail
echo "ℹ️  Route security is declared in kong/kong.yml."
echo "   Run ./scripts/kong-setup.sh to apply changes."
exec ./scripts/kong-setup.sh

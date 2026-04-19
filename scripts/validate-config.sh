#!/usr/bin/env bash
# Pre-deploy configuration sanity checks.
set -uo pipefail

echo "🔍 Gateway Configuration Validation"
echo "===================================="
ERRORS=0; WARNINGS=0
err()  { echo "❌ $*"; ERRORS=$((ERRORS+1)); }
warn() { echo "⚠️  $*"; WARNINGS=$((WARNINGS+1)); }
ok()   { echo "✅ $*"; }

# --- Docker ---
docker info >/dev/null 2>&1 && ok "Docker is running" || err "Docker is not running"

# --- docker-compose ---
if docker compose config -q 2>/dev/null; then
  ok "docker-compose.yml valid"
else
  err "docker-compose.yml invalid (run 'docker compose config')"
fi

# --- .env ---
if [ ! -f .env ]; then
  warn ".env missing - using .env.example"
else
  ok ".env present"
  if grep -qE "CHANGE_ME|REPLACE_ME|your_.*_here|dev_samsara_webhook_secret_not_for_production" .env; then
    warn ".env contains placeholder / dev values - replace before pointing real traffic here"
  fi
  if grep -qE "^(POSTGRES_PASSWORD|KONG_PG_PASSWORD|REDIS_PASSWORD|N8N_BASIC_AUTH_PASSWORD|GF_SECURITY_ADMIN_PASSWORD)=.{0,15}$" .env; then
    warn "At least one password in .env is shorter than 16 chars"
  fi
  # block known-bad defaults
  if grep -qE "admin.*password|_dev_password|=admin$" .env; then
    warn "Weak/default credentials detected in .env"
  fi
fi

# --- Kong declarative ---
if [ -f kong/kong.yml ]; then
  ok "kong/kong.yml present"
  if grep -q "REPLACE_ME_.*_api_key" kong/kong.yml; then
    warn "kong/kong.yml still contains placeholder API keys"
  fi
  if grep -q "gateway.example.com" kong/kong.yml; then
    warn "kong/kong.yml CORS origin still references example.com - pin to real caller origin"
  fi
  if ! grep -q "name: key-auth" kong/kong.yml; then
    err "kong/kong.yml missing global key-auth plugin"
  fi
  if ! grep -q "name: rate-limiting" kong/kong.yml; then
    err "kong/kong.yml missing rate-limiting plugin"
  fi
else
  err "kong/kong.yml missing"
fi

# --- Unigroup Converge ---
if [ -f .env ]; then
  if grep -qE "^EDI_(SENDER|RECEIVER)_ID=" .env; then
    warn ".env still has legacy EDI_SENDER_ID/EDI_RECEIVER_ID - Unigroup Converge uses OAuth2, not EDI. Migrate to UNIGROUP_CLIENT_ID/SECRET."
  fi
  if grep -qE "^UNIGROUP_CLIENT_ID=(CHANGE_ME|)$" .env; then
    warn "UNIGROUP_CLIENT_ID not set - Unigroup outbound calls will fail until creds land"
  fi
  case "$(grep -E '^UNIGROUP_ENV=' .env | cut -d= -f2)" in
    staging|production) ok "UNIGROUP_ENV valid" ;;
    "") warn "UNIGROUP_ENV unset (defaulting to staging)" ;;
    *) err "UNIGROUP_ENV must be 'staging' or 'production'" ;;
  esac
fi

# --- Alertmanager rendering ---
if [ -f config/alertmanager/alertmanager.yml.tpl ] && [ -f config/alertmanager/entrypoint.sh ]; then
  ok "alertmanager template + entrypoint present"
  if [ -f .env ]; then
    if ! grep -qE "^ALERTMANAGER_SLACK_WEBHOOK=https://hooks\.slack\.com/" .env && \
       ! grep -qE "^ALERTMANAGER_PAGERDUTY_KEY=.{20,}$" .env; then
      warn "Neither ALERTMANAGER_SLACK_WEBHOOK nor ALERTMANAGER_PAGERDUTY_KEY is set - alerts will fire to /dev/null"
    fi
  fi
else
  err "config/alertmanager/alertmanager.yml.tpl or entrypoint.sh missing"
fi

# --- TLS certs ---
if [ -f config/kong/certs/server.crt ] && [ -f config/kong/certs/server.key ]; then
  ok "Kong TLS cert+key present"
else
  warn "Kong TLS cert missing (config/kong/certs/server.{crt,key}). HTTPS won't start."
fi

# --- Observability configs ---
for f in config/prometheus/prometheus.yml \
         config/prometheus/rules/gateway.rules.yml \
         config/alertmanager/alertmanager.yml \
         config/loki/loki-config.yaml \
         config/promtail/promtail.yml \
         config/grafana/provisioning/datasources/datasources.yml; do
  [ -f "$f" ] && ok "$f" || err "$f missing"
done

# --- Scripts executable ---
for s in backup.sh restore.sh create-topics.sh test.sh kong-setup.sh; do
  [ -x "scripts/$s" ] && ok "scripts/$s executable" || warn "scripts/$s not executable"
done

# --- Git hygiene ---
if [ -d .git ]; then
  git check-ignore .env >/dev/null 2>&1 && ok ".env is gitignored" || err ".env NOT gitignored"
  if git log --all --full-history -- .env 2>/dev/null | grep -q "^commit"; then
    err ".env appears in git history - rotate secrets and rewrite history"
  fi
  # Block Samsara token pattern anywhere in tree
  if grep -REn --exclude-dir=.git --exclude='*.md' --exclude='.env.example' \
       "samsara_api_[A-Za-z0-9]{20,}" . >/dev/null 2>&1; then
    err "Samsara API token pattern present in source tree"
  fi
fi

echo ""
echo "===================================="
if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
  echo "✅ All checks passed"
  exit 0
elif [ "$ERRORS" -eq 0 ]; then
  echo "⚠️  $WARNINGS warning(s)"
  exit 0
else
  echo "❌ $ERRORS error(s), $WARNINGS warning(s)"
  exit 1
fi

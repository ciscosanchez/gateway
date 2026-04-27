#!/usr/bin/env bash
# provision.sh — one-shot setup for a fresh Ubuntu 22.04/24.04 server.
#
# Usage (run as root on the target box):
#   curl -fsSL https://raw.githubusercontent.com/.../scripts/provision.sh | bash
#   -- or --
#   git clone <repo> gateway && cd gateway && sudo bash scripts/provision.sh
#
# What it does:
#   1. Installs Docker CE + Compose plugin
#   2. Creates a non-root 'gateway' user in the docker group
#   3. Configures UFW: SSH + Kong proxy public; admin ports stay loopback-only
#   4. Copies .env.example → .env (single-node defaults wired in)
#   5. Generates a self-signed TLS cert (replace with real cert for production)
#   6. Starts the full stack
#   7. Prints SSH tunnel commands for all admin UIs
#
# Single-node note: Redpanda replication is set to 1 and topics are created
# with --replicas 1. Everything else is identical to the multi-node compose.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}▶ $*${NC}"; }
ok()    { echo -e "${GREEN}✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $*${NC}"; }
die()   { echo -e "${RED}✗ $*${NC}"; exit 1; }

# ── Require root ────────────────────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] || die "Run as root: sudo bash scripts/provision.sh"

GATEWAY_USER="${GATEWAY_USER:-gateway}"
REPO_DIR="${REPO_DIR:-/opt/gateway}"

# ── Detect OS ───────────────────────────────────────────────────────────────
if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_VERSION="${VERSION_ID:-0}"
else
  die "Cannot detect OS — /etc/os-release missing"
fi

[[ "$OS_ID" =~ ^(ubuntu|debian)$ ]] || \
  die "Only Ubuntu/Debian supported (got $OS_ID $OS_VERSION)"

info "Provisioning on $OS_ID $OS_VERSION"

# ── System update ───────────────────────────────────────────────────────────
info "Updating package lists…"
apt-get update -qq

info "Installing base dependencies…"
apt-get install -y -qq \
  ca-certificates curl gnupg lsb-release git openssl ufw fail2ban \
  2>/dev/null
ok "Base packages installed"

# ── Docker CE ───────────────────────────────────────────────────────────────
if command -v docker &>/dev/null; then
  ok "Docker already installed: $(docker --version)"
else
  info "Installing Docker CE from official repo…"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/${OS_ID} $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  ok "Docker installed: $(docker --version)"
fi

# ── Gateway user ─────────────────────────────────────────────────────────────
if id "$GATEWAY_USER" &>/dev/null; then
  ok "User '$GATEWAY_USER' already exists"
else
  info "Creating user '$GATEWAY_USER'…"
  useradd -m -s /bin/bash -G docker "$GATEWAY_USER"
  ok "User '$GATEWAY_USER' created (docker group)"
fi
# Also add current SSH user to docker group if present
if [ -n "${SUDO_USER:-}" ] && id "$SUDO_USER" &>/dev/null; then
  usermod -aG docker "$SUDO_USER" 2>/dev/null || true
fi

# ── Clone or locate repo ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POSSIBLE_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$POSSIBLE_ROOT/docker-compose.yml" ]; then
  info "Repo found at $POSSIBLE_ROOT — using in place"
  REPO_DIR="$POSSIBLE_ROOT"
elif [ -d "$REPO_DIR" ]; then
  ok "Repo already at $REPO_DIR"
else
  die "No repo found at $POSSIBLE_ROOT or $REPO_DIR.\nClone the repo first:\n  git clone <repo-url> $REPO_DIR"
fi

cd "$REPO_DIR"

# ── Firewall (UFW) ────────────────────────────────────────────────────────────
info "Configuring UFW firewall…"
ufw --force reset >/dev/null

# Essential
ufw allow 22/tcp   comment 'SSH'
# Kong public proxy (HTTPS inbound webhooks + API)
ufw allow 8443/tcp comment 'Kong HTTPS proxy'
ufw allow 8000/tcp comment 'Kong HTTP proxy (redirect to HTTPS)'
# Block everything else by default
ufw default deny incoming
ufw default allow outgoing
ufw --force enable

ok "UFW active — public: 22, 8000, 8443 | admin ports stay loopback-only"

# ── fail2ban ─────────────────────────────────────────────────────────────────
systemctl enable --now fail2ban 2>/dev/null || true

# ── .env ─────────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  info "Copying .env.example → .env (single-node defaults)…"
  cp .env.example .env
  # Single-node Redpanda: replication must be 1
  sed -i 's/^#\?REDPANDA_DEFAULT_REPLICAS=.*/REDPANDA_DEFAULT_REPLICAS=1/' .env 2>/dev/null || true
  grep -q "REDPANDA_DEFAULT_REPLICAS" .env || echo "REDPANDA_DEFAULT_REPLICAS=1" >> .env
  warn "Review .env and replace all CHANGE_ME values before exposing to the internet"
else
  ok ".env already present — skipping copy"
  # Ensure single-node replicas even if .env existed
  grep -q "REDPANDA_DEFAULT_REPLICAS" .env || echo "REDPANDA_DEFAULT_REPLICAS=1" >> .env
fi

# ── TLS cert ─────────────────────────────────────────────────────────────────
if [ ! -f config/kong/certs/server.crt ]; then
  info "Generating self-signed TLS cert (replace with real cert for production)…"
  mkdir -p config/kong/certs
  SERVER_IP="$(curl -4 -fsSL --max-time 5 ifconfig.me 2>/dev/null || echo 'localhost')"
  openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
    -keyout config/kong/certs/server.key \
    -out    config/kong/certs/server.crt \
    -subj "/CN=${SERVER_IP}" \
    -addext "subjectAltName=IP:${SERVER_IP},DNS:localhost" 2>/dev/null
  chmod 600 config/kong/certs/server.key
  ok "Self-signed cert for ${SERVER_IP} (valid 1 year)"
  warn "Replace with a Let's Encrypt cert: certbot --standalone -d <your-domain>"
else
  ok "TLS cert already present"
fi

# ── Fix file ownership ────────────────────────────────────────────────────────
chown -R "$GATEWAY_USER:$GATEWAY_USER" "$REPO_DIR"
chmod +x scripts/*.sh 2>/dev/null || true

# ── Start the stack ───────────────────────────────────────────────────────────
info "Starting gateway stack (this may take a few minutes on first pull)…"

# Single-node: override Redpanda replicas for topic creation
export REDPANDA_DEFAULT_REPLICAS=1

docker compose pull --quiet 2>/dev/null || true
docker compose up -d --wait --wait-timeout 300

ok "Stack is up"

# ── Create topics (single-node: replicas=1) ───────────────────────────────────
info "Creating Redpanda topics (replicas=1 for single-node)…"
REPLICAS=1 bash scripts/create-topics.sh || warn "Topic creation had warnings — check above"

# ── Bootstrap n8n ─────────────────────────────────────────────────────────────
info "Bootstrapping n8n (waiting for it to be ready)…"
bash scripts/n8n-bootstrap.sh || warn "n8n bootstrap had warnings — may need a manual retry"

# ── Print access summary ──────────────────────────────────────────────────────
SERVER_IP="$(curl -4 -fsSL --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Gateway stack is running on ${SERVER_IP}${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Public endpoints (Kong):${NC}"
echo "  HTTPS proxy:  https://${SERVER_IP}:8443/{samsara,netsuite,tai,wms,unigroup}"
echo "  Requires:     X-API-Key header"
echo ""
echo -e "${CYAN}Admin UIs — loopback only. Access via SSH tunnel:${NC}"
echo ""
echo "  # Paste this into a NEW terminal on your laptop:"
echo "  ssh -L 5678:127.0.0.1:5678 \\"
echo "      -L 8001:127.0.0.1:8001 \\"
echo "      -L 8080:127.0.0.1:8080 \\"
echo "      -L 3002:127.0.0.1:3002 \\"
echo "      -L 7070:127.0.0.1:7070 \\"
echo "      -L 9090:127.0.0.1:9090 \\"
echo "      -N root@${SERVER_IP}"
echo ""
echo "  Then open in your browser:"
echo "    http://localhost:7070   ← Gateway Admin UI"
echo "    http://localhost:5678   ← n8n"
echo "    http://localhost:3002   ← Grafana"
echo "    http://localhost:8080   ← Redpanda Console"
echo "    http://localhost:9090   ← Prometheus"
echo "    http://localhost:8001   ← Kong Admin API"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Edit .env — replace CHANGE_ME values with real credentials"
echo "  2. docker compose --profile admin up -d  (start the Admin UI)"
echo "  3. Run ./scripts/test.sh to smoke-test"
echo "  4. Replace the self-signed cert with a real one for production"
echo ""

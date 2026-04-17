# Gateway — make targets for local POC demos and prod ops.

.PHONY: demo up down nuke tunnel test topics kong-reload logs ps validate

COMPOSE_LITE = docker compose -f docker-compose.yml -f docker-compose.lite.yml
COMPOSE_HA   = docker compose --profile ha --profile logs

demo:
	@test -f .env || (echo "Create .env from .env.example first" && exit 1)
	$(COMPOSE_LITE) up -d --wait
	@echo ""
	@echo "Lite stack is up. Next:"
	@echo "  make topics      # create kafka topics"
	@echo "  make kong-reload # load declarative config"
	@echo "  make tunnel      # expose publicly via cloudflared"

up:
	@test -f .env || (echo "Create .env from .env.example first" && exit 1)
	$(COMPOSE_HA) up -d --wait

down:
	docker compose down

nuke:
	docker compose down -v --remove-orphans

tunnel:
	@command -v cloudflared >/dev/null || (echo "install cloudflared: brew install cloudflared" && exit 1)
	cloudflared tunnel --url https://localhost:443 --no-tls-verify

test:
	./scripts/test.sh

topics:
	./scripts/create-topics.sh

kong-reload:
	./scripts/kong-setup.sh

logs:
	docker compose logs -f --tail=100 kong n8n

ps:
	docker compose ps

validate:
	./scripts/validate-config.sh

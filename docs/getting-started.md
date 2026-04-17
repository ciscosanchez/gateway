# Getting Started Checklist

## Prerequisites

- [ ] Install Docker Desktop
  - Mac: https://www.docker.com/products/docker-desktop/
  - Windows: https://www.docker.com/products/docker-desktop/
  - Linux: `sudo apt install docker.io`

- [ ] Start Docker Desktop (verify icon in system tray/menu bar)

## Setup Steps

### 1. Start the Stack

```bash
# Start all services
./scripts/setup.sh

# Or manually:
docker compose up -d
```

**Wait 1-2 minutes** for all services to become healthy.

### 2. Verify Services

```bash
docker compose ps
```

All services should show "Up (healthy)".

### 3. Access UIs

Open in your browser:

- [ ] n8n: http://localhost:5678 (admin/admin)
- [ ] Kong Manager: http://localhost:8002
- [ ] Redpanda Console: http://localhost:8080
- [ ] Grafana: http://localhost:3000 (admin/admin)

### 4. Configure Kong Routes

```bash
./scripts/kong-setup.sh
```

This creates routes for:
- `/samsara` → n8n webhooks
- `/netsuite` → n8n webhooks
- `/wms` → n8n webhooks
- `/unigroup` → n8n webhooks

### 5. Create Kafka Topics

```bash
./scripts/create-topics.sh
```

This creates topics:
- `samsara-events`
- `orders`
- `inventory`
- `edi-outbound`
- `netsuite-updates`
- `wms-events`
- `errors-dlq` (dead letter queue)

### 6. Test Hello World

```bash
# Test Kong is proxying
curl http://localhost:8000/samsara

# You should get a 404 from n8n (webhook not yet created)
# This is expected! It means Kong → n8n routing works.
```

## Next Steps

### For NetSuite Integration

- [ ] Read `docs/netsuite-integration.md`
- [ ] Enable Token-Based Auth in NetSuite
- [ ] Create integration record and access tokens
- [ ] Add credentials to `.env` file
- [ ] Create n8n workflow to test NetSuite API

### For Samsara Integration

- [ ] Get Samsara API token
- [ ] Register webhook in Samsara dashboard
- [ ] Point webhook to: `http://your-domain:8000/samsara`
- [ ] Create n8n workflow to handle Samsara events
- [ ] Test with Samsara webhook test tool

### For WMS Integration

- [ ] Document your WMS API
- [ ] Create Kong route for WMS
- [ ] Build n8n workflow: WMS → transform → NetSuite
- [ ] Set up Kafka topic for WMS events

### Build Your First Workflow

1. Open n8n: http://localhost:5678
2. Create new workflow
3. Add "Webhook" trigger node
   - Method: POST
   - Path: `samsara`
4. Add "HTTP Request" node
   - Method: POST
   - URL: `http://redpanda:28082/topics/samsara-events`
   - Body: `{{ $json }}`
5. Activate workflow
6. Test:
   ```bash
   curl -X POST http://localhost:8000/samsara \
     -H "Content-Type: application/json" \
     -d '{"test": "data"}'
   ```
7. Check Redpanda Console to see the message

## Troubleshooting

### Docker daemon not running

```
Error: Cannot connect to the Docker daemon
```

**Fix**: Start Docker Desktop

### Port already in use

```
Error: Bind for 0.0.0.0:8000 failed: port is already allocated
```

**Fix**: Change the port in `docker-compose.yml` or stop the conflicting service

### Service unhealthy

```bash
# Check logs
docker compose logs kong
docker compose logs n8n
docker compose logs redpanda

# Restart service
docker compose restart kong
```

### Reset everything

```bash
docker compose down -v
docker compose up -d
```

## Daily Development Workflow

```bash
# Start services
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down

# Restart a service
docker compose restart n8n

# Execute commands in containers
docker exec -it gateway-redpanda rpk topic list
docker exec -it gateway-postgres psql -U gateway -d gateway
```

## Resources

- **Main README**: `README.md`
- **NetSuite Guide**: `docs/netsuite-integration.md`
- **Kong Docs**: https://docs.konghq.com/
- **n8n Docs**: https://docs.n8n.io/
- **Redpanda Docs**: https://docs.redpanda.com/

## Support

If you get stuck:

1. Check the logs: `docker compose logs -f [service-name]`
2. Check service health: `docker compose ps`
3. Check Kong admin: http://localhost:8001
4. Check Redpanda console: http://localhost:8080
5. Restart the service: `docker compose restart [service-name]`

## Production Readiness

Before going to production:

- [ ] Change all default passwords
- [ ] Set up proper SSL/TLS certificates
- [ ] Configure backup strategy for PostgreSQL
- [ ] Set up HashiCorp Vault for secrets
- [ ] Configure production-grade Redpanda cluster (3+ nodes)
- [ ] Set up proper log aggregation
- [ ] Configure alerting in Grafana
- [ ] Review and harden security settings
- [ ] Set resource limits in docker-compose
- [ ] Document runbooks for common issues

# Deployment Checklist

Use this checklist before deploying to staging or production.

## Pre-Deployment Validation

### Configuration
- [ ] Run `./scripts/validate-config.sh` - all checks pass
- [ ] `.env` file configured with real values (not placeholders)
- [ ] All secrets rotated from development values
- [ ] Secrets stored in secret manager (not in `.env` file)
- [ ] Docker image versions pinned (no `:latest` tags)
- [ ] Resource limits configured for all services

### Security
- [ ] Kong Admin API (8001, 8002) not publicly exposed
- [ ] TLS/HTTPS certificates installed
- [ ] API authentication enabled on all routes
- [ ] Rate limiting configured per route
- [ ] Strong passwords (64+ characters) on all services
- [ ] IP whitelisting configured where needed
- [ ] CORS policies configured correctly
- [ ] Security headers added (X-Frame-Options, CSP, etc.)

### High Availability
- [ ] Redpanda running in cluster mode (3+ nodes)
- [ ] PostgreSQL using managed service or replication
- [ ] Kong running with 2+ instances
- [ ] Load balancer configured in front of Kong
- [ ] Health checks configured for all services
- [ ] Restart policies set to `unless-stopped`

### Backup & Recovery
- [ ] Backup script tested: `./scripts/backup.sh`
- [ ] Restore script tested: `./scripts/restore.sh`
- [ ] Backups scheduled (daily recommended)
- [ ] Backup retention policy defined
- [ ] Recovery time objective (RTO) documented
- [ ] Recovery point objective (RPO) documented
- [ ] Disaster recovery plan documented

### Monitoring & Alerting
- [ ] Grafana dashboards created and working
- [ ] Prometheus scraping all services
- [ ] Loki receiving logs from all services
- [ ] Alerts configured for:
  - [ ] API error rate > 5%
  - [ ] Response time p95 > 2s
  - [ ] Redpanda consumer lag > 10k
  - [ ] Disk usage > 80%
  - [ ] Memory usage > 90%
  - [ ] Service health check failures
- [ ] Alert notification channels configured (Slack/PagerDuty/email)
- [ ] On-call rotation defined

### Testing
- [ ] Smoke tests pass: `./scripts/test.sh`
- [ ] Integration tests completed
- [ ] Load testing completed (target: 5k req/sec)
- [ ] Failover testing completed
- [ ] Workflow executions tested end-to-end
- [ ] Dead letter queue processing tested

### Documentation
- [ ] Architecture diagram up to date
- [ ] All integrations documented
- [ ] Runbook created for common issues
- [ ] Escalation procedures documented
- [ ] Contact information current

### Network & Infrastructure
- [ ] DNS configured correctly
- [ ] Firewall rules applied
- [ ] VPN access configured for admin access
- [ ] SSL/TLS certificates valid (not expired)
- [ ] CDN configured (if applicable)

### Compliance
- [ ] Security review completed
- [ ] Penetration testing completed (if required)
- [ ] Compliance requirements met (SOC 2, HIPAA, etc.)
- [ ] Data retention policies configured
- [ ] Audit logging enabled

## Deployment Steps

### 1. Pre-Deployment
```bash
# Validate configuration
./scripts/validate-config.sh

# Create backup of current state
./scripts/backup.sh

# Tag the release
git tag -a v1.0.0 -m "Production release v1.0.0"
git push origin v1.0.0
```

### 2. Deploy
```bash
# Pull latest code
git pull origin main

# Start services
docker compose up -d

# Wait for health checks
sleep 60
docker compose ps

# Verify all services are healthy
./scripts/test.sh
```

### 3. Post-Deployment
```bash
# Set up Kong routes and security
./scripts/kong-setup.sh
./scripts/secure-kong-routes.sh

# Verify workflows
# Open http://localhost:5678 and test each workflow

# Monitor for 15 minutes
# Watch Grafana dashboards, check for errors
```

### 4. Rollback (if needed)
```bash
# Stop new version
docker compose down

# Restore from backup
./scripts/restore.sh ./backups/<timestamp>

# Restart previous version
git checkout <previous-tag>
docker compose up -d
```

## Post-Deployment Monitoring

### First Hour
- [ ] Monitor error rates (should be < 0.1%)
- [ ] Monitor response times (p95 < 500ms)
- [ ] Check Redpanda consumer lag (should be near 0)
- [ ] Verify workflows executing successfully
- [ ] Check disk/memory/CPU usage

### First Day
- [ ] Review all error logs
- [ ] Verify all scheduled workflows ran
- [ ] Check backup completed successfully
- [ ] Review security alerts
- [ ] Verify integrations working end-to-end

### First Week
- [ ] Review weekly metrics
- [ ] Optimize based on traffic patterns
- [ ] Update documentation with learnings
- [ ] Schedule retrospective meeting

## Rollback Criteria

Immediately rollback if:
- Error rate > 5% for 5+ minutes
- Critical workflow failure rate > 10%
- Response time p95 > 5 seconds for 10+ minutes
- Database connection failures
- Redpanda cluster unhealthy
- Security incident detected

## Emergency Contacts

| Role | Name | Phone | Email |
|------|------|-------|-------|
| On-Call Engineer | | | |
| DevOps Lead | | | |
| Security Team | | | |
| Manager | | | |

## Useful Commands

```bash
# Check service status
docker compose ps

# View logs
docker compose logs -f kong n8n redpanda

# Restart a service
docker compose restart <service>

# Check Kong routes
curl http://localhost:8001/routes

# Check Redpanda topics
docker exec -it gateway-redpanda rpk topic list

# Force backup
./scripts/backup.sh

# Access n8n
open http://localhost:5678

# Access Grafana
open http://localhost:3002
```

## Production-Specific Notes

**DO NOT:**
- Expose Kong Admin API (8001, 8002) to public internet
- Use default passwords
- Deploy without testing backups
- Skip load testing
- Deploy on Friday afternoon

**DO:**
- Use managed services where possible (RDS, Cloud SQL, etc.)
- Set up proper monitoring before launch
- Have rollback plan ready
- Test disaster recovery procedures
- Keep documentation up to date

---

**Last Updated:** April 17, 2026  
**Next Review:** [Set date for quarterly review]

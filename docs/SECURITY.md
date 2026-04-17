# Security Guide

## ⚠️ CRITICAL: Development vs Production

**This setup is currently configured for LOCAL DEVELOPMENT ONLY.**

Do NOT run this in production without implementing the security controls below.

---

## Critical Security Issues to Address Before Production

### 1. Secrets Management

**Current State:** Secrets are in plaintext `.env` file  
**Risk:** Credential leakage, unauthorized access  
**Production Solution:**

- Use a secrets manager:
  - AWS Secrets Manager
  - HashiCorp Vault
  - Azure Key Vault
  - Google Cloud Secret Manager

- Docker secrets (for Docker Swarm):
  ```yaml
  secrets:
    samsara_token:
      external: true
  ```

- Kubernetes secrets (for K8s):
  ```yaml
  apiVersion: v1
  kind: Secret
  metadata:
    name: api-secrets
  data:
    SAMSARA_API_TOKEN: <base64-encoded>
  ```

**Action Required:**
1. Rotate ALL secrets immediately (especially the Samsara token if it was committed)
2. Never commit real secrets to git
3. Use `.env.example` template only

---

### 2. Kong Admin API Security

**Current State:** Kong Admin API (port 8001) and Manager (port 8002) have NO authentication  
**Risk:** Anyone with network access can modify routes, services, and plugins  

**Production Solution:**

#### Option A: Network Isolation (Recommended)
```yaml
# docker-compose.yml - Remove public port exposure
kong:
  ports:
    - "8000:8000"    # Keep proxy public
    - "8443:8443"    # Keep proxy HTTPS public
    # DO NOT expose 8001 and 8002 publicly
  # Access admin via docker exec or internal VPN only
```

#### Option B: Add RBAC + Authentication
```bash
# Enable Kong RBAC
curl -X POST http://localhost:8001/rbac/users \
  --data name=admin \
  --data user_token=secure-random-token-here

# Add Basic Auth to Admin API
curl -X POST http://localhost:8001/services/admin-api/plugins \
  --data "name=basic-auth"
```

#### Option C: Use Kong Manager Enterprise (requires license)
- Built-in RBAC
- SSO support
- Audit logging

**Action Required:**
1. Remove ports 8001, 8002 from public exposure
2. Access Kong Admin only through:
   - VPN
   - SSH tunnel: `ssh -L 8001:localhost:8001 your-server`
   - Private network only

---

### 3. TLS/HTTPS Configuration

**Current State:** All traffic is HTTP (plaintext)  
**Risk:** Credentials leaked, man-in-the-middle attacks  

**Production Solution:**

#### Enable HTTPS in Kong
```bash
# Create certificate (use real cert in production)
openssl req -x509 -newkey rsa:4096 \
  -keyout key.pem -out cert.pem \
  -days 365 -nodes \
  -subj "/CN=yourdomain.com"

# Add certificate to Kong
curl -X POST http://localhost:8001/certificates \
  --form "cert=@cert.pem" \
  --form "key=@key.pem" \
  --form "snis[]=yourdomain.com"

# Update routes to HTTPS only
curl -X PATCH http://localhost:8001/routes/{route-id} \
  --data "protocols[]=https"
```

#### Update n8n for HTTPS
```yaml
# docker-compose.yml
n8n:
  environment:
    - N8N_PROTOCOL=https
    - WEBHOOK_URL=https://yourdomain.com/
```

**Action Required:**
1. Obtain SSL/TLS certificates (Let's Encrypt, your CA, etc.)
2. Configure Kong to terminate TLS
3. Set `https_redirect_status_code: 301` to force HTTPS
4. Update all webhook URLs to HTTPS

---

### 4. Authentication & Rate Limiting

**Current State:** No auth or rate limiting on Kong routes  
**Risk:** Abuse, DDoS, unauthorized access  

**Production Solution:**

#### Add API Key Authentication
```bash
# Enable key-auth plugin on a route
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=key-auth"

# Create API key for a consumer
curl -X POST http://localhost:8001/consumers/your-app/key-auth \
  --data "key=your-secure-api-key"

# Use in requests
curl -H "apikey: your-secure-api-key" https://yourdomain.com/samsara
```

#### Add Rate Limiting
```bash
# Limit to 100 requests per minute
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=rate-limiting" \
  --data "config.minute=100" \
  --data "config.policy=local"
```

#### Add IP Restriction
```bash
# Only allow specific IPs
curl -X POST http://localhost:8001/routes/samsara-route/plugins \
  --data "name=ip-restriction" \
  --data "config.allow=10.0.0.0/8,192.168.0.0/16"
```

**Example Configuration:**
See [Kong Plugin Examples](kong-plugins.md) for complete examples.

**Action Required:**
1. Add `key-auth` to all public routes
2. Add rate limiting per integration
3. Consider OAuth 2.0 for user-facing APIs

---

### 5. Database Security

**Current State:** Weak default passwords  
**Risk:** Database compromise, data breach  

**Production Passwords:**
```bash
# Generate strong passwords
openssl rand -base64 32

# Never use:
POSTGRES_PASSWORD=gateway_dev_password  # ❌ BAD
POSTGRES_PASSWORD=kong_dev_password     # ❌ BAD

# Use:
POSTGRES_PASSWORD=<64-char-random-string>  # ✅ GOOD
```

**Additional Controls:**
- Enable SSL for PostgreSQL connections
- Restrict database network access
- Regular backups with encryption
- Implement database audit logging

---

## Audit Findings Summary

### Critical (Fix Before ANY Production Traffic)
- [ ] Rotate all secrets and use secret manager
- [ ] Lock down Kong Admin API (remove public exposure)
- [ ] Enable TLS/HTTPS end-to-end
- [ ] Add authentication to all routes
- [ ] Replace all default passwords

### High (Fix Before Significant Traffic)
- [ ] Add rate limiting
- [ ] Configure multi-node Redpanda cluster
- [ ] Add resource limits to containers
- [ ] Implement proper backup/restore procedures

### Medium (Important for Production)
- [ ] Configure Grafana dashboards and alerts
- [ ] Set up centralized logging
- [ ] Add workflow retry and DLQ handling
- [ ] Implement CI/CD pipeline

---

## Production Checklist

Before going live:

- [ ] All secrets rotated and stored in secret manager
- [ ] Kong Admin API not publicly accessible
- [ ] TLS certificates installed and HTTPS enforced
- [ ] API authentication enabled on all routes
- [ ] Rate limiting configured per integration
- [ ] Strong passwords on all databases
- [ ] Resource limits set on all containers
- [ ] Monitoring dashboards live
- [ ] Alerting configured (Slack/PagerDuty)
- [ ] Backups tested and scheduled
- [ ] Incident response plan documented
- [ ] Load tested with realistic traffic
- [ ] Security scan completed (no critical vulnerabilities)

---

## Security Resources

- [Kong Security Best Practices](https://docs.konghq.com/gateway/latest/production/security/)
- [n8n Security Documentation](https://docs.n8n.io/hosting/security/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)

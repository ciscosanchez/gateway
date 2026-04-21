# CI Security Scanning

## What runs on every push

Four jobs, all blocking (push fails if any fails):

| Job | What it checks |
|---|---|
| **Validate configs** | `docker compose config`, Kong declarative parse, workflow JSON syntax, YAML lint, shellcheck |
| **Secret scanning** | gitleaks full history scan + grep for known token patterns |
| **Trivy scans** | Config misconfigs (HIGH/CRITICAL), filesystem secrets+vulns, image CVEs (CRITICAL fixable only) |
| **Smoke test** | Full stack up, Kong health, Prometheus health, Samsara replay through Kong→n8n→Kafka |

---

## Trivy image scanning

### How the image list works

The Trivy image scan step derives its image list **live from `docker-compose.yml`**:

```bash
images=$(grep -E '^\s+image: [a-zA-Z]' docker-compose.yml \
  | awk '{print $2}' \
  | grep -v '^gateway' \
  | sort -u)
```

This means **updating an image version in `docker-compose.yml` is the only step required** — CI automatically picks it up. The old approach had a hardcoded list in `.github/workflows/ci.yml` that diverged silently from compose (this is what caused CI to keep scanning n8n:1.28.0 even after it was bumped to 2.18.0).

Locally-built images (names starting with `gateway`) are excluded — they're built from our own Dockerfiles which are covered by the `trivy config` scan step.

### When a new CVE blocks CI

1. **Check if an upstream fix exists**: look at the `Fixed Version` column in the Trivy output. If there's a fix, upgrade the image in `docker-compose.yml`.
2. **Verify the upgrade is clean**: scan the candidate locally before committing:
   ```bash
   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
     aquasec/trivy:latest image --quiet \
     --severity CRITICAL --ignore-unfixed \
     <image>:<new-tag>
   ```
3. **If no upstream fix exists** (or upgrading introduces new CVEs): add to `.trivyignore` with rationale. See the template below.
4. **If it's a transitive dep in an upstream image** we can't control: add to `.trivyignore`.

### Adding to `.trivyignore`

Every entry needs a comment explaining **why** it was accepted. Template:

```
# --- Short description of what's affected ---
# CVE-XXXX-NNNNN: <one-line description of the vuln>; fixed in <version>.
# <Why we can't just upgrade> and <why the risk is acceptable in this stack>.
# Mitigation: <what reduces the blast radius — loopback-only, auth-gated, etc.>
CVE-XXXX-NNNNN         # short label
```

**Never add a CVE without a comment.** A bare CVE ID with no context becomes unmaintainable.

### Quarterly review

`.trivyignore` has a `LAST REVIEWED` date at the top. On the first Monday of each quarter:

1. Re-run CI or manually scan each image in `.trivyignore`.
2. For each accepted CVE, check whether upstream has published a fix.
3. Delete any CVE that's now fixed (upgrade the image or confirm it disappeared).
4. Update `LAST REVIEWED`.

---

## Accepted CVE log

Current accepted risks (see `.trivyignore` for full rationale):

| CVE | Affected image | Why accepted |
|---|---|---|
| `AVD-DS-0002` | `admin-ui` Dockerfile | Intentionally runs as root for `.env` write + docker.sock; profile-gated loopback-only |
| `CVE-2025-15467` | `redpandadata/redpanda` | OpenSSL RCE; upstream rebuild pending; TLS terminates at Kong |
| `CVE-2025-21613` | `redpandadata/console` | go-git arg injection; Console is loopback-only and has no git URL code paths |
| `CVE-2024-8986` | `grafana/grafana` | Plugin SDK info leak; Grafana is loopback-only, no untrusted plugins installed |
| `CVE-2026-41242` | `n8nio/n8n` | protobufjs RCE; transitive dep, n8n hasn't bumped it yet; n8n is auth-gated loopback |
| Go stdlib CVEs | `loki`, `promtail`, `prometheus`, `alertmanager` | In Go binaries compiled against older stdlib; upstream rebuild required |

---

## History

| Date | Change |
|---|---|
| 2026-04-19 | Initial CI setup; hardcoded image list; n8n:1.28.0 CVEs added to trivyignore |
| 2026-04-21 | Upgraded n8n 1.28.0 → 2.18.0 (fixed 33 CRITICAL CVEs). Made image list dynamic from docker-compose.yml. Added Redpanda, Grafana, Redpanda Console CVEs surfaced by wider scan coverage. |

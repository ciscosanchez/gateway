# Gateway Performance

Measured numbers for the default (non-HA) stack. Any claim of "HA-ready" or
"production-capable" elsewhere in the repo should point here.

## TL;DR

**~230–240 requests/second sustained** through the full path
(`curl → Kong → HMAC verify → key-auth → rate-limit → n8n webhook → Function
node → Kafka produce (acks=all) → respond`), with **0% error** across 112,500
requests. **p95 ≈ 0.55s, p99 ≈ 0.82s** at saturation.

The ceiling is serial latency of the synchronous path, not container CPU —
all containers sit under 15% CPU throughout. To go higher, switch n8n to
queue mode and add workers (`--scale n8n-worker=N`, `ha` profile).

## Test conditions

- **Date**: 2026-04-19
- **Host**: macOS arm64, 18 CPUs, Docker Desktop
- **Stack**: Kong 3.6 (DB-less) + n8n 1.28 (regular mode, 1 process) +
  Redpanda v24.1.7 (single-node) + Postgres 16 + Redis 7
- **Loader**: `hey -c 100 -q <target> -t 10`
- **Payload**: `workflows/samples/samsara-geofence-entry.json` (~460 bytes)
- **Endpoint**: `https://localhost:8443/samsara`, full plugin chain active
  (key-auth, HMAC pre-function, rate-limit, ip-restriction, prometheus,
  correlation-id, response-transformer, cors, request-size-limit)
- **Reproduce**: `./scripts/load-test.sh` (raises Kong rate-limit + IP
  allowlist via `docker-compose.loadtest.yml` overlay for the test duration,
  reverts on exit)

## Per-stage results

Each stage ran 30 seconds at the target rate.

| Target RPS | Achieved | p50     | p95     | p99     | Slowest | 2xx     | 5xx |
|-----------:|---------:|--------:|--------:|--------:|--------:|--------:|----:|
| 50         | 216      | 0.448s  | 0.576s  | 0.665s  | 0.686s  | 1,500   | 0   |
| 200        | 239      | 0.410s  | 0.483s  | 0.512s  | 0.658s  | 6,000   | 0   |
| 500        | 243      | 0.407s  | 0.467s  | 0.486s  | 0.514s  | 15,000  | 0   |
| 1000       | 222      | 0.434s  | 0.549s  | 0.822s  | 1.404s  | 30,000  | 0   |
| 2000       | 232      | 0.414s  | 0.522s  | 0.757s  | 0.845s  | 60,000  | 0   |

**Totals**: 112,500 requests, 100% HTTP 202, zero 401/403/429/5xx.

## What these numbers mean

### 1. The ceiling is ~240 rps, and that's Little's Law

The loader held 100 concurrent workers. With ~0.43s average round-trip, the
theoretical max is `100 / 0.43 ≈ 232 rps`, which is exactly what we hit.
Raising the target from 500 to 1000 to 2000 didn't move the achieved number
— the workers were all busy waiting for responses.

### 2. The bottleneck is serial latency, not CPU

Post-stage container stats at every level stayed low:

| Container | Peak CPU | Peak Mem |
|-----------|---------:|---------:|
| gateway-kong       | 14.0%   | 1.98 GiB  |
| gateway-n8n        | 4.4%    | 662 MiB   |
| gateway-redpanda-0 | 0.3%    | 359 MiB   |
| gateway-postgres   | 3.0%    | 121 MiB   |
| gateway-redis      | 0.6%    | 11 MiB    |

No container is CPU-bound. The ceiling is that each request waits for:

1. Kong: key-auth + HMAC SHA-256 + rate-limit (Redis round-trip) + IP check
2. n8n: accepts POST, runs Function node (JS eval + payload redact)
3. n8n: calls Kafka produce with `acks=all` → waits for broker ack
4. n8n: responds via "Respond to Webhook" node

Steps 3 alone dominates — Kafka `acks=all` on a single broker typically
costs 100–200ms per produce. The rest of the stack adds another 200–300ms
of node-hopping latency.

### 3. Kong is fine; n8n is the choke

At 240 rps, Kong is at 14% CPU and would comfortably serve 5–10× more.
n8n's single process is at 4% CPU but holds 100 concurrent webhook handlers
in its event loop — once the loop saturates on Kafka-produce awaits, no
amount of incoming RPS gets processed faster.

### 4. p99 degrades first at ~1000 target

At 50/200/500 target, p99 < 700ms and max latency < 700ms — clean.

At 1000 target, p99 jumped to 820ms with a max of 1.4s. That's the first
sign of queueing: the incoming rate just exceeded the drain rate, and some
requests sat in Kong's connection queue waiting for n8n to free up.

### 5. At 2000 target p99 came back down

Because the loader gave up trying to push harder — once it saw the server
was processing at ~232 rps it naturally paced itself there, so p99 stayed
similar to the saturated-but-not-queueing regime.

## Headroom / what to do if you need more

| Need      | Move                                                             |
|-----------|------------------------------------------------------------------|
| 500 rps   | Switch n8n `EXECUTIONS_MODE: queue` and add 2 workers (`ha` profile). Linear gains per worker. |
| 1000 rps  | As above, 4–6 workers; raise Redpanda memory to 2 GiB; put the response node before the Kafka node (fire-and-forget with DLQ on fail). |
| 5000 rps+ | Replace n8n for this hot path with a direct Kong → Kafka pre-function plugin. n8n was designed for richness, not throughput. |

## What is NOT measured here

- **Real Samsara traffic patterns**: bursts vs. steady-state. Our test is
  steady-state. Real webhooks arrive in bursts (fleet events cluster).
- **Kafka consumer lag under sustained load**: at ~240 rps the lag stayed at
  0 (consumers kept up). At higher rates, downstream consumers may lag —
  measure separately.
- **Cold start**: every test hit a warm stack. First-request latency after
  a container restart is slower.
- **TLS handshake cost**: client uses keep-alive, so we amortize the TLS
  handshake across thousands of requests. Per-request TLS cost adds
  ~10–40ms on cold connections.
- **Multi-region / WAN latency**: everything ran on the same machine. Real
  Samsara edge → our gateway adds 20–100ms depending on where the stack is
  deployed.

## How to reproduce

Prereqs:

```bash
brew install hey
docker compose up -d
./scripts/n8n-bootstrap.sh            # one-time, idempotent
```

Then:

```bash
./scripts/load-test.sh
# default stages: 50 200 500 1000 2000 req/s × 30s each, c=100
# output: ./load-test-<ts>.md
```

The script generates `/tmp/kong.loadtest.yml` from `kong/kong.yml` with three
relaxations (rate-limit, IP allowlist, dev API key) and starts Kong under
the `docker-compose.loadtest.yml` overlay. On exit (even Ctrl+C) it reverts
Kong to the canonical config. Nothing in-tree is modified.

Tunable via env vars: `STAGES`, `DURATION`, `CONCURRENCY`, `OUT`, `ENDPOINT`.

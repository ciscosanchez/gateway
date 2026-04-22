"""Central integration registry.

This is the ONE place to define an integration. The env, kong, n8n, and
healthcheck modules all derive their lookup tables from here — adding a new
integration is a single block below, nothing else.

Adding an integration:
  1. Add an Integration(...) entry to INTEGRATIONS at the bottom of this file.
  2. If you need a health probe, add a probe function in healthchecks.py and
     pass it as the `probe` argument.  Simple API-key probes can use the
     factory helpers defined here so healthchecks.py stays thin.
  3. That's it. env.py, kong_api.py, and healthchecks.py derive everything
     automatically from this list.

Field reference:
  Integration.name          -- display name used in the UI (must be unique)
  Integration.env_vars      -- list of EnvVar defining which .env keys belong
  Integration.kong_consumer -- username in kong.yml (or None if no Kong key)
  Integration.n8n_types     -- list of n8n credential type ids that map to
                               this integration (TYPE_TO_INTEGRATION in n8n_api)
  Integration.probe         -- optional Callable[[], Tuple[bool, str]] — see
                               healthchecks.py for the signature contract
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

# ── service shorthand ────────────────────────────────────────────────────────
N8N   = ["n8n", "n8n-worker"]
KONG  = ["kong"]
INFRA = ["postgres"] + N8N + ["postgres-exporter"]
REDIS = ["redis", "kong"] + N8N + ["redis-exporter"]


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class EnvVar:
    name: str
    kind: str        # "secret" | "identifier" | "config"
    services: list   # compose services that must restart when this changes


@dataclass
class Integration:
    name: str
    env_vars: list[EnvVar]              = field(default_factory=list)
    kong_consumer: Optional[str]        = None   # e.g. "tai-client"
    n8n_types: list[str]                = field(default_factory=list)
    probe: Optional[Callable[[], Tuple[bool, str]]] = None
    key: str                            = ""     # URL slug; auto-derived from name if empty
    label: str                          = ""     # display name; falls back to name if empty
    description: str                    = ""     # one-line card subtitle shown in dashboard grid
    notes: str                          = ""     # longer summary shown in integration detail panel
    hidden: bool                        = False  # exclude from UI (infra/platform integrations)


# ── registry ─────────────────────────────────────────────────────────────────
# Probes are wired in healthchecks.py to avoid a circular import;
# the Integration objects below leave probe=None and healthchecks patches them.

INTEGRATIONS: list[Integration] = [

    Integration(
        name="Samsara",
        key="samsara",
        description="Inbound webhook · HMAC-SHA256",
        notes=(
            "Inbound webhook flow. Kong verifies HMAC-SHA256 signature before n8n "
            "sees the payload. Events are normalised in n8n and published to the "
            "samsara-events Redpanda topic. PII (driver names, addresses) is "
            "stripped before publish."
        ),
        env_vars=[
            EnvVar("SAMSARA_API_TOKEN",      "secret", N8N),
            EnvVar("SAMSARA_WEBHOOK_SECRET", "secret", N8N + KONG),
        ],
        kong_consumer="samsara-client",
    ),

    Integration(
        name="NetSuite",
        key="netsuite",
        description="OAuth1 TBA · bidirectional",
        notes=(
            "Outbound workflow (netsuite-create-sales-order.json) is complete. "
            "Blocked on OAuth1 TBA credentials: NETSUITE_CONSUMER_KEY / _SECRET / "
            "TOKEN_ID / TOKEN_SECRET. Inbound webhook path /netsuite is wired via "
            "netsuite-webhook-to-kafka.json."
        ),
        env_vars=[
            EnvVar("NETSUITE_ACCOUNT_ID",      "identifier", N8N),
            EnvVar("NETSUITE_CONSUMER_KEY",    "secret",     N8N),
            EnvVar("NETSUITE_CONSUMER_SECRET", "secret",     N8N),
            EnvVar("NETSUITE_TOKEN_ID",        "secret",     N8N),
            EnvVar("NETSUITE_TOKEN_SECRET",    "secret",     N8N),
        ],
        kong_consumer="netsuite-client",
        n8n_types=["oAuth1Api"],
    ),

    Integration(
        name="Unigroup",
        key="unigroup",
        description="OAuth2 + GraphQL · outbound",
        notes=(
            "OAuth2 client_credentials + GraphQL. Outbound workflow scaffolded. "
            "Blocked on Keycloak client credentials from Unigroup: "
            "UNIGROUP_CLIENT_ID / UNIGROUP_CLIENT_SECRET. Also need: scope value "
            "and confirmation whether Converge pushes webhooks or we poll."
        ),
        env_vars=[
            EnvVar("UNIGROUP_ENV",           "config", N8N),
            EnvVar("UNIGROUP_CLIENT_ID",     "secret", N8N),
            EnvVar("UNIGROUP_CLIENT_SECRET", "secret", N8N),
            EnvVar("UNIGROUP_OAUTH_SCOPE",   "config", N8N),
        ],
        kong_consumer="unigroup-client",
    ),

    Integration(
        name="Tai",
        key="tai",
        label="Tai TMS",
        description="API key · inbound + outbound",
        notes=(
            "Inbound webhooks accepted at /tai (API key auth via Kong). Events "
            "routed to tai-bills, tai-invoices, tai-shipments, tai-customers, "
            "tai-carriers topics. Outbound workers consume tai-out and tai-updates "
            "to push back to the Tai REST API. Live in beta — key rotates 1st of "
            "each month. Contact: Kyle Wang."
        ),
        env_vars=[
            EnvVar("TAI_API_URL",         "config", N8N),
            EnvVar("TAI_API_KEY",         "secret", N8N),
            EnvVar("TAI_INBOUND_API_KEY", "secret", KONG),
        ],
        kong_consumer="tai-client",
    ),

    Integration(
        name="WMS",
        key="wms",
        description="REST · outbound (stub)",
        notes="Route exists in kong.yml. No workflow wired yet.",
        env_vars=[
            EnvVar("WMS_API_KEY", "secret", N8N),
            EnvVar("WMS_API_URL", "config", N8N),
        ],
        kong_consumer="wms-client",
    ),

    Integration(
        name="Dispatch",
        key="dispatch",
        description="REST · outbound (stub)",
        notes="Not scaffolded yet.",
        env_vars=[
            EnvVar("DISPATCH_API_KEY", "secret", N8N),
            EnvVar("DISPATCH_API_URL", "config", N8N),
        ],
        kong_consumer="dispatch-client",
    ),

    # ── Infrastructure / platform (not external integrations) ────────────────
    Integration(
        name="Infra",
        hidden=True,
        env_vars=[
            EnvVar("POSTGRES_PASSWORD",       "secret", INFRA),
            EnvVar("REDIS_PASSWORD",          "secret", REDIS),
            EnvVar("N8N_BASIC_AUTH_PASSWORD", "secret", N8N),
            EnvVar("N8N_ENCRYPTION_KEY",      "secret", N8N),
            EnvVar("GF_SECURITY_ADMIN_PASSWORD", "secret", ["grafana"]),
            EnvVar("ADMIN_UI_USER",           "identifier", []),
            EnvVar("ADMIN_UI_PASSWORD",       "secret",     []),
        ],
        n8n_types=["httpBasicAuth", "httpHeaderAuth", "kafka", "redisKey", "postgres"],
    ),

    Integration(
        name="Alerting",
        hidden=True,
        env_vars=[
            EnvVar("ALERTMANAGER_SLACK_WEBHOOK", "secret", ["alertmanager"]),
            EnvVar("ALERTMANAGER_PAGERDUTY_KEY", "secret", ["alertmanager"]),
        ],
        n8n_types=["slackApi", "pagerDutyApi"],
    ),

    Integration(
        name="Zammad",
        hidden=True,
        env_vars=[
            EnvVar("ZAMMAD_URL",       "config", N8N),
            EnvVar("ZAMMAD_API_TOKEN", "secret", N8N),
            EnvVar("ZAMMAD_GROUP",     "config", N8N),
            EnvVar("ZAMMAD_CUSTOMER",  "config", N8N),
        ],
    ),

    Integration(
        name="Backup",
        hidden=True,
        env_vars=[
            EnvVar("BACKUP_AGE_RECIPIENT", "config", []),
        ],
    ),
]


# ── derived lookup tables (used by env.py, kong_api.py, n8n_api.py) ─────────

# { "TAI_API_KEY": {"integration": "Tai", "kind": "secret", "services": [...]} }
ENV_REGISTRY: dict[str, dict] = {
    ev.name: {
        "integration": intg.name,
        "kind":        ev.kind,
        "services":    ev.services,
    }
    for intg in INTEGRATIONS
    for ev in intg.env_vars
}

# { "tai-client": "Tai" }
KONG_CONSUMER_MAP: dict[str, str] = {
    intg.kong_consumer: intg.name
    for intg in INTEGRATIONS
    if intg.kong_consumer
}

# { "oAuth1Api": "NetSuite" }
N8N_TYPE_MAP: dict[str, str] = {
    t: intg.name
    for intg in INTEGRATIONS
    for t in intg.n8n_types
}

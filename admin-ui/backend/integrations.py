"""Central integration registry.

This is the ONE place to define an integration. Everything — env vars, Kong
consumers, n8n credential types, health probes, topics, field schemas, and
field-level mappings — is declared here. All other modules derive their
lookup tables from this list.

Adding an integration:
  1. Add an Integration(...) entry to INTEGRATIONS below.
  2. If you need a health probe, add a probe function in healthchecks.py and
     register it in _PROBE_FUNCTIONS there.
  3. That's it.

Field reference:
  name            -- stable internal identifier (used as credential 'integration'
                     field, probe key, approval records). Never rename after first
                     activation — use label for display changes instead.
  key             -- URL slug (auto-derived from name if empty)
  label           -- display name shown in UI (falls back to name)
  description     -- one-line card subtitle
  notes           -- longer summary for detail panel
  hidden          -- exclude from UI (infra/platform entries)
  env_vars        -- .env keys that belong to this integration
  kong_consumer   -- Kong consumer username (or None)
  n8n_types       -- n8n credential type ids
  probe           -- connection health probe (wired in healthchecks.py)
  topics          -- Redpanda topics this integration publishes to / subscribes from
  field_schema    -- canonical event shape as a simple field list
  transformations -- source-field → canonical-field mapping rules
  n8n_workflow_ids-- n8n workflow IDs that implement this integration
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
class TopicDef:
    name: str
    direction: str          # "publish" | "subscribe"
    description: str
    retention_ms: int = 604_800_000   # 7 days; -1 = infinite


@dataclass
class FieldMapping:
    source: str             # dot-path in raw source payload, e.g. "vehicle.id"
    target: str             # dot-path in canonical schema, e.g. "vehicle_id"
    transform: str          # "passthrough" | "rename" | "redact" | "drop"
    note: str = ""


@dataclass
class SchemaField:
    name: str
    type: str               # "string" | "number" | "boolean" | "object" | "array"
    description: str
    required: bool = True


@dataclass
class Integration:
    name: str
    env_vars: list[EnvVar]              = field(default_factory=list)
    kong_consumer: Optional[str]        = None
    n8n_types: list[str]                = field(default_factory=list)
    probe: Optional[Callable[[], Tuple[bool, str]]] = None
    key: str                            = ""
    label: str                          = ""
    description: str                    = ""
    notes: str                          = ""
    hidden: bool                        = False
    topics: list[TopicDef]              = field(default_factory=list)
    field_schema: list[SchemaField]     = field(default_factory=list)
    transformations: list[FieldMapping] = field(default_factory=list)
    n8n_workflow_ids: list[str]         = field(default_factory=list)


# ── registry ─────────────────────────────────────────────────────────────────
# Probes are wired in healthchecks.py to avoid a circular import.

INTEGRATIONS: list[Integration] = [

    Integration(
        name="Samsara",
        key="samsara",
        description="Inbound webhook · HMAC-SHA256",
        notes=(
            "Inbound webhook flow. Kong verifies HMAC-SHA256 signature before n8n "
            "sees the payload. Events are normalised and published to the "
            "samsara-events topic. PII (driver names, formatted addresses) is "
            "stripped before publish — only IDs, coordinates, speed, and heading "
            "are retained."
        ),
        env_vars=[
            EnvVar("SAMSARA_API_TOKEN",      "secret", N8N),
            EnvVar("SAMSARA_WEBHOOK_SECRET", "secret", N8N + KONG),
        ],
        kong_consumer="samsara-client",
        n8n_workflow_ids=["samsara-webhook-to-kafka"],
        topics=[
            TopicDef(
                name="samsara-events",
                direction="publish",
                description="All normalised inbound Samsara webhook events.",
            ),
        ],
        field_schema=[
            SchemaField("event_type",  "string",  "Samsara event type, e.g. geofenceEntry"),
            SchemaField("vehicle_id",  "string",  "Samsara vehicle ID"),
            SchemaField("driver_id",   "string",  "Samsara driver ID (PII-stripped to ID only)"),
            SchemaField("timestamp",   "string",  "ISO-8601 event time"),
            SchemaField("lat",         "number",  "Latitude at event time"),
            SchemaField("lng",         "number",  "Longitude at event time"),
            SchemaField("speed_mph",   "number",  "Speed in mph", required=False),
            SchemaField("heading_deg", "number",  "Heading in degrees", required=False),
        ],
        transformations=[
            FieldMapping("eventType",                    "event_type",  "rename"),
            FieldMapping("vehicle.id",                   "vehicle_id",  "rename"),
            FieldMapping("driver.id",                    "driver_id",   "rename"),
            FieldMapping("driver.name",                  "driver_name", "redact",  "PII — stripped"),
            FieldMapping("occurred_at",                  "timestamp",   "rename"),
            FieldMapping("location.latitude",            "lat",         "rename"),
            FieldMapping("location.longitude",           "lng",         "rename"),
            FieldMapping("vehicle.gps.speedMilesPerHour","speed_mph",   "rename"),
            FieldMapping("vehicle.gps.headingDegrees",   "heading_deg", "rename"),
        ],
    ),

    Integration(
        name="NetSuite",
        key="netsuite",
        description="OAuth1 TBA · bidirectional",
        notes=(
            "Outbound: n8n workflow consumes netsuite-outbound topic and creates "
            "records in NetSuite via OAuth1 TBA. Inbound: /netsuite webhook path "
            "is wired — NetSuite change events arrive via webhook and are published "
            "to netsuite-inbound. Blocked on OAuth1 TBA credentials."
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
        n8n_workflow_ids=["netsuite-webhook-to-kafka", "netsuite-create-sales-order"],
        topics=[
            TopicDef(
                name="netsuite-inbound",
                direction="publish",
                description="Change events from NetSuite webhooks.",
            ),
            TopicDef(
                name="netsuite-outbound",
                direction="subscribe",
                description="Records to create or update in NetSuite (sales orders, invoices).",
            ),
        ],
        field_schema=[
            SchemaField("record_type", "string", "NetSuite record type, e.g. salesOrder"),
            SchemaField("record_id",   "string", "NetSuite internal ID"),
            SchemaField("action",      "string", "create | update | delete"),
            SchemaField("timestamp",   "string", "ISO-8601 event time"),
            SchemaField("data",        "object", "Record fields; shape varies by record_type"),
        ],
        transformations=[
            FieldMapping("type",          "record_type", "rename"),
            FieldMapping("id",            "record_id",   "rename"),
            FieldMapping("changeType",    "action",      "rename"),
            FieldMapping("changeDate",    "timestamp",   "rename"),
            FieldMapping("fields",        "data",        "passthrough"),
        ],
    ),

    Integration(
        name="Unigroup",
        key="unigroup",
        label="Unigroup Converge",
        description="OAuth2 + GraphQL · outbound",
        notes=(
            "OAuth2 client_credentials flow. n8n workflow scaffolded to consume "
            "unigroup-outbound topic and push moves/dispatches to Unigroup Converge "
            "via GraphQL. Blocked on UNIGROUP_CLIENT_ID / UNIGROUP_CLIENT_SECRET "
            "and scope confirmation from Unigroup."
        ),
        env_vars=[
            EnvVar("UNIGROUP_ENV",           "config", N8N),
            EnvVar("UNIGROUP_CLIENT_ID",     "secret", N8N),
            EnvVar("UNIGROUP_CLIENT_SECRET", "secret", N8N),
            EnvVar("UNIGROUP_OAUTH_SCOPE",   "config", N8N),
        ],
        kong_consumer="unigroup-client",
        n8n_workflow_ids=["unigroup-outbound-processor"],
        topics=[
            TopicDef(
                name="unigroup-outbound",
                direction="subscribe",
                description="Move and dispatch records to push to Unigroup Converge.",
            ),
        ],
        field_schema=[
            SchemaField("move_id",      "string", "Internal move reference"),
            SchemaField("move_type",    "string", "local | longhaul | shuttle"),
            SchemaField("origin",       "object", "Origin location {address, lat, lng}"),
            SchemaField("destination",  "object", "Destination location {address, lat, lng}"),
            SchemaField("scheduled_at", "string", "ISO-8601 scheduled pickup time"),
            SchemaField("customer_id",  "string", "Customer reference"),
        ],
        transformations=[
            FieldMapping("id",           "move_id",      "rename"),
            FieldMapping("type",         "move_type",    "rename"),
            FieldMapping("from",         "origin",       "rename"),
            FieldMapping("to",           "destination",  "rename"),
            FieldMapping("pickup_time",  "scheduled_at", "rename"),
            FieldMapping("customer",     "customer_id",  "rename"),
        ],
    ),

    Integration(
        name="Tai",
        key="tai",
        label="Tai TMS",
        description="API key · inbound + outbound",
        notes=(
            "Inbound webhooks accepted at /tai (API key auth via Kong). Events "
            "routed by webhook_type to tai-bills, tai-invoices, tai-shipments, "
            "tai-customers, tai-carriers. Outbound workers consume tai-out and "
            "tai-updates and push back to Tai REST API. Live in beta — key rotates "
            "1st of each month. Contact: Kyle Wang."
        ),
        env_vars=[
            EnvVar("TAI_API_URL",         "config", N8N),
            EnvVar("TAI_API_KEY",         "secret", N8N),
            EnvVar("TAI_INBOUND_API_KEY", "secret", KONG),
        ],
        kong_consumer="tai-client",
        n8n_workflow_ids=["tai-webhook-to-kafka", "tai-outbound-processor"],
        topics=[
            TopicDef("tai-bills",      "publish",   "Bill records from Tai inbound webhooks."),
            TopicDef("tai-invoices",   "publish",   "Invoice records from Tai inbound webhooks."),
            TopicDef("tai-shipments",  "publish",   "Shipment records from Tai inbound webhooks."),
            TopicDef("tai-customers",  "publish",   "Customer records from Tai inbound webhooks."),
            TopicDef("tai-carriers",   "publish",   "Carrier records from Tai inbound webhooks."),
            TopicDef("tai-out",        "subscribe", "Outbound payloads to POST back to Tai API."),
            TopicDef("tai-updates",    "subscribe", "Partial update payloads to PATCH in Tai API."),
        ],
        field_schema=[
            SchemaField("webhook_type",    "string", "Tai webhook type, e.g. bill_created"),
            SchemaField("correlation_id",  "string", "X-Correlation-Id echo from Kong"),
            SchemaField("entity_id",       "string", "Tai entity primary key"),
            SchemaField("entity_type",     "string", "bill | invoice | shipment | customer | carrier"),
            SchemaField("timestamp",       "string", "ISO-8601 event time"),
            SchemaField("payload",         "object", "Full entity snapshot from Tai; shape varies by entity_type"),
        ],
        transformations=[
            FieldMapping("webhook_type",    "webhook_type",   "passthrough"),
            FieldMapping("correlationId",   "correlation_id", "rename"),
            FieldMapping("data.id",         "entity_id",      "rename"),
            FieldMapping("data.type",       "entity_type",    "rename"),
            FieldMapping("timestamp",       "timestamp",      "passthrough"),
            FieldMapping("data",            "payload",        "passthrough"),
        ],
    ),

    Integration(
        name="WMS",
        key="wms",
        description="REST · outbound (stub)",
        notes=(
            "Route exists in kong.yml. Env vars defined. No n8n workflow wired yet. "
            "Intended to receive pick/pack/ship instructions from Redpanda and push "
            "them to the warehouse management system REST API."
        ),
        env_vars=[
            EnvVar("WMS_API_KEY", "secret", N8N),
            EnvVar("WMS_API_URL", "config", N8N),
        ],
        kong_consumer="wms-client",
        topics=[
            TopicDef(
                name="wms-outbound",
                direction="subscribe",
                description="Pick, pack, and ship instructions to push to WMS.",
            ),
        ],
        field_schema=[
            SchemaField("order_id",    "string", "Internal order reference"),
            SchemaField("action",      "string", "pick | pack | ship | receive"),
            SchemaField("sku",         "string", "Product SKU"),
            SchemaField("quantity",    "number", "Unit quantity"),
            SchemaField("location",    "string", "Warehouse bin/location code"),
            SchemaField("timestamp",   "string", "ISO-8601 instruction time"),
        ],
        transformations=[],
    ),

    Integration(
        name="Dispatch",
        key="dispatch",
        description="REST · outbound (stub)",
        notes="Not scaffolded yet. Intended to push driver dispatch instructions from Redpanda to the dispatch platform.",
        env_vars=[
            EnvVar("DISPATCH_API_KEY", "secret", N8N),
            EnvVar("DISPATCH_API_URL", "config", N8N),
        ],
        kong_consumer="dispatch-client",
        topics=[
            TopicDef(
                name="dispatch-outbound",
                direction="subscribe",
                description="Driver dispatch instructions to push to the dispatch platform.",
            ),
        ],
        field_schema=[
            SchemaField("dispatch_id", "string", "Dispatch instruction ID"),
            SchemaField("driver_id",   "string", "Driver to assign"),
            SchemaField("vehicle_id",  "string", "Vehicle to use"),
            SchemaField("pickup",      "object", "Pickup location {address, lat, lng, time}"),
            SchemaField("dropoff",     "object", "Dropoff location {address, lat, lng, time}"),
            SchemaField("load_id",     "string", "Associated load/order ID"),
        ],
        transformations=[],
    ),

    # ── Infrastructure / platform ────────────────────────────────────────────
    Integration(
        name="Infra",
        hidden=True,
        env_vars=[
            EnvVar("POSTGRES_PASSWORD",          "secret", INFRA),
            EnvVar("REDIS_PASSWORD",             "secret", REDIS),
            EnvVar("N8N_BASIC_AUTH_PASSWORD",    "secret", N8N),
            EnvVar("N8N_ENCRYPTION_KEY",         "secret", N8N),
            EnvVar("GF_SECURITY_ADMIN_PASSWORD", "secret", ["grafana"]),
            EnvVar("ADMIN_UI_USER",              "identifier", []),
            EnvVar("ADMIN_UI_PASSWORD",          "secret",     []),
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


# ── derived lookup tables ────────────────────────────────────────────────────

ENV_REGISTRY: dict[str, dict] = {
    ev.name: {
        "integration": intg.name,
        "kind":        ev.kind,
        "services":    ev.services,
    }
    for intg in INTEGRATIONS
    for ev in intg.env_vars
}

KONG_CONSUMER_MAP: dict[str, str] = {
    intg.kong_consumer: intg.name
    for intg in INTEGRATIONS
    if intg.kong_consumer
}

N8N_TYPE_MAP: dict[str, str] = {
    t: intg.name
    for intg in INTEGRATIONS
    for t in intg.n8n_types
}

# Fast key → Integration lookup
BY_KEY: dict[str, Integration] = {
    (intg.key or intg.name.lower()): intg
    for intg in INTEGRATIONS
}

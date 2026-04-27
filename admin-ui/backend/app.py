"""Gateway Admin UI backend.

Phase A: read-only env source.
Phase B: frontend wires up to /api/credentials.
Phase C: writes to .env + append-only SQLite audit log.
Phases D/E: n8n + Kong sources with their own write paths.
Phase 2 (this commit): HTTP Basic auth gate on /api/* when
  ADMIN_UI_USER / ADMIN_UI_PASSWORD are set in the environment. If either
  is unset the gate is disabled (defaults only suitable for throwaway
  local dev); compose injects real values in production.

Safety rails:
- Plaintext values are never echoed back in responses.
- Writes are atomic; failed writes leave the old state intact.
- Writes to unknown variable names / consumers / n8n types are rejected.
- Audit rows store sha256 hashes of before/after, not the values.
"""
from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path
from typing import Optional

import traceback

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

import audit
import healthchecks
import services as svc_mod
from sources.env import (
    EnvWriteError,
    UnknownCredential,
    count_by_status,
    delete_env_credential,
    list_env_credentials,
    services_for,
    set_env_credential,
)
from sources.n8n_api  import (
    N8NError,
    UnknownN8NType,
    WRITABLE_TYPES as N8N_WRITABLE_TYPES,
    delete_n8n_credential,
    is_reachable as n8n_reachable,
    list_n8n_credentials,
    set_n8n_credential,
)
from sources.kong_api import (
    KongWriteError,
    UnknownKongConsumer,
    delete_kong_key,
    is_reachable as kong_reachable,
    list_kong_credentials,
    set_kong_key,
)

app = FastAPI(
    title="Gateway Admin",
    version="0.2.0-phase-c",
    description="Unified credential / integration management for the gateway stack.",
)

audit.init_schema()

STATIC_DIR = Path(os.getenv("ADMIN_STATIC_DIR", "/app/static"))

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
# Labels use the matched route *template* (e.g. /api/credentials/env/{name})
# instead of the actual path, to cap cardinality. Status is the response code
# as a string so Prometheus doesn't need numeric conversions for regex matches.

admin_requests_total = Counter(
    "admin_requests_total",
    "Total HTTP requests handled by admin-ui backend.",
    ["path", "method", "status"],
)
admin_errors_total = Counter(
    "admin_errors_total",
    "Requests that returned 5xx OR raised unhandled exceptions.",
    ["path", "method", "status"],
)
admin_request_seconds = Histogram(
    "admin_request_seconds",
    "Request duration in seconds.",
    ["path", "method"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)


def _route_template(request: Request) -> str:
    """Return the matched route template so label cardinality stays bounded.

    Unmatched paths (404s) fall back to "<unmatched>" rather than the raw URL.
    """
    r = request.scope.get("route")
    if r is not None and hasattr(r, "path"):
        return r.path
    return "<unmatched>"

# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------
# If both ADMIN_UI_USER and ADMIN_UI_PASSWORD are set, every /api/* endpoint
# requires HTTP Basic auth matching them. When either is unset, the gate is
# a no-op so local-dev / wireframe mode keeps working. Production compose
# injects real values from .env.

ADMIN_UI_USER     = os.getenv("ADMIN_UI_USER", "")
ADMIN_UI_PASSWORD = os.getenv("ADMIN_UI_PASSWORD", "")
_AUTH_ENABLED     = bool(ADMIN_UI_USER and ADMIN_UI_PASSWORD)

_http_basic = HTTPBasic(auto_error=False)


def _current_actor(
    credentials: Optional[HTTPBasicCredentials] = Depends(_http_basic),
) -> str:
    """Validate basic-auth credentials; return the user name for audit rows.

    - Gate disabled (no envs) -> return "admin" (backwards compatible).
    - Gate enabled + valid    -> return the username.
    - Gate enabled + invalid  -> 401 with WWW-Authenticate so browsers prompt.
    """
    if not _AUTH_ENABLED:
        return "admin"
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin auth required",
            headers={"WWW-Authenticate": 'Basic realm="Gateway Admin"'},
        )
    # constant-time compare to sidestep trivial timing oracles
    user_ok = secrets.compare_digest(credentials.username, ADMIN_UI_USER)
    pass_ok = secrets.compare_digest(credentials.password, ADMIN_UI_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Gateway Admin"'},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# Health + meta
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    import time
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        path = _route_template(request)
        admin_errors_total.labels(path=path, method=request.method, status="500").inc()
        admin_requests_total.labels(path=path, method=request.method, status="500").inc()
        print(f"[admin-ui] UNHANDLED {request.method} {path}: {exc}\n{traceback.format_exc()}", flush=True)
        return JSONResponse(status_code=500, content={"detail": "internal error"})
    finally:
        admin_request_seconds.labels(path=_route_template(request), method=request.method).observe(time.perf_counter() - started)
    path   = _route_template(request)
    status_str = str(response.status_code)
    admin_requests_total.labels(path=path, method=request.method, status=status_str).inc()
    if response.status_code >= 500:
        admin_errors_total.labels(path=path, method=request.method, status=status_str).inc()
    return response


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus scrape endpoint. Intentionally unauthenticated: the admin-ui
    container is only reachable on the internal docker network (127.0.0.1:7070
    from the host, no external exposure) and Prometheus scrapes it by service
    name. If we ever expose admin-ui beyond loopback, gate this too.
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/health", tags=["meta"])
def health(actor: str = Depends(_current_actor)) -> dict:
    docker_ok = False
    try:
        svc_mod.client().ping()
        docker_ok = True
    except Exception:
        pass
    return {
        "status": "ok",
        "phase":  "1-n8n-writes",
        "sources": {
            "env":  {"enabled": True,              "writable": True},
            "n8n":  {"enabled": n8n_reachable(),  "writable": n8n_reachable()},
            "kong": {"enabled": kong_reachable(), "writable": kong_reachable()},
        },
        "n8n_types": {k: v for k, v in N8N_WRITABLE_TYPES.items()},
        "restart": {"enabled": docker_ok},
    }


@app.get("/api/version", tags=["meta"])
def version(actor: str = Depends(_current_actor)) -> dict:
    return {"version": app.version, "auth_enabled": _AUTH_ENABLED}


# ---------------------------------------------------------------------------
# Credentials (read-only)
# ---------------------------------------------------------------------------

@app.get("/api/credentials", tags=["credentials"])
def list_credentials(
    source: Optional[str] = Query(default=None, description="env | n8n | kong"),
    integration: Optional[str] = Query(default=None),
    actor: str = Depends(_current_actor),
) -> dict:
    items: list[dict] = []
    if source in (None, "env"):
        items.extend(list_env_credentials())
    if source in (None, "n8n"):
        items.extend(list_n8n_credentials())
    if source in (None, "kong"):
        items.extend(list_kong_credentials())
    if integration:
        items = [i for i in items if (i.get("integration") or "").lower() == integration.lower()]
    return {
        "items":    items,
        "count":    len(items),
        "by_status": count_by_status(items),
    }


@app.get("/api/credentials/{name}", tags=["credentials"])
def get_credential(name: str, actor: str = Depends(_current_actor)) -> dict:
    for item in list_env_credentials():
        if item["name"] == name:
            return item
    raise HTTPException(status_code=404, detail=f"credential '{name}' not found")


class UpsertReq(BaseModel):
    value: str = Field(..., description="New plaintext value. Never echoed back.")
    note:  Optional[str] = Field(default=None, description="Free-form audit note")


def _client_ip(req: Request) -> str:
    return req.client.host if req.client else "unknown"


@app.post("/api/credentials/env/{name}/rotate", tags=["credentials"])
def rotate_env_credential(
    name: str, body: UpsertReq, req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    """Write the new value, run the integration's probe, and roll back if the
    probe fails. Only the integration's own probe runs here; an unrelated
    env var (no probe) is treated as trivially verified.
    """
    # Remember the old value for rollback (read from disk - dotenv)
    from dotenv import dotenv_values
    from sources.env import ENV_FILE
    old_raw = (dotenv_values(str(ENV_FILE)).get(name) or "").strip()

    # Write the new value through the normal path
    try:
        saved = set_env_credential(name, body.value)
    except UnknownCredential as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EnvWriteError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Run the matching probe (if any) against the fresh .env
    integration = saved.get("integration") or ""
    probe_key = healthchecks.probe_for_integration(integration)
    probe = healthchecks.run(probe_key) if probe_key else {
        "ok": True, "latency_ms": 0, "detail": "no probe defined for this integration — write applied without verification",
    }

    if not probe["ok"]:
        # Rollback: restore the previous value on disk
        try:
            set_env_credential(name, old_raw)
        except EnvWriteError as rollback_err:
            audit.record(
                action="rollback_failed", source="env", name=name,
                integration=integration, note=f"probe: {probe['detail']}; rollback: {rollback_err}",
                client_ip=_client_ip(req), actor=actor,
            )
            raise HTTPException(
                status_code=500,
                detail=f"probe failed AND rollback failed: {probe['detail']} | rollback: {rollback_err}",
            )
        audit.record(
            action="rotate_failed", source="env", name=name,
            integration=integration, note=f"probe: {probe['detail']}",
            client_ip=_client_ip(req), actor=actor,
        )
        raise HTTPException(
            status_code=400,
            detail={"message": "probe rejected new value; rolled back", "probe": probe},
        )

    # Probe passed — record the rotate + schedule a restart
    before = saved.pop("_before_hash", None)
    after  = saved.pop("_after_hash", None)
    audit.record(
        action="rotate", source="env", name=name,
        integration=integration, before_hash=before, after_hash=after,
        note=body.note or f"probe ok in {probe['latency_ms']}ms",
        client_ip=_client_ip(req), actor=actor,
    )
    audit.mark_restart_pending(services_for(name))
    return {"credential": saved, "probe": probe}


@app.put("/api/credentials/env/{name}", tags=["credentials"])
def upsert_env_credential(
    name: str, body: UpsertReq, req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    try:
        saved = set_env_credential(name, body.value)
    except UnknownCredential as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EnvWriteError as e:
        raise HTTPException(status_code=500, detail=str(e))
    before = saved.pop("_before_hash", None)
    after  = saved.pop("_after_hash", None)
    audit.record(
        action="create" if before is None else "update",
        source="env",
        name=name,
        integration=saved.get("integration"),
        before_hash=before,
        after_hash=after,
        note=body.note,
        client_ip=_client_ip(req),
        actor=actor,
    )
    audit.mark_restart_pending(services_for(name))
    return saved


@app.delete("/api/credentials/env/{name}", tags=["credentials"])
def clear_env_credential(
    name: str, req: Request, note: Optional[str] = None,
    actor: str = Depends(_current_actor),
) -> dict:
    try:
        cleared = delete_env_credential(name)
    except UnknownCredential as e:
        raise HTTPException(status_code=404, detail=str(e))
    except EnvWriteError as e:
        raise HTTPException(status_code=500, detail=str(e))
    before = cleared.pop("_before_hash", None)
    cleared.pop("_after_hash", None)
    audit.record(
        action="delete",
        source="env",
        name=name,
        integration=cleared.get("integration"),
        before_hash=before,
        after_hash=None,
        note=note,
        client_ip=_client_ip(req),
        actor=actor,
    )
    audit.mark_restart_pending(services_for(name))
    return cleared


# ---------------------------------------------------------------------------
# n8n credentials
# ---------------------------------------------------------------------------

class N8NUpsertReq(BaseModel):
    type: str = Field(..., description="n8n credential type (see /api/health.n8n_types)")
    data: dict = Field(default_factory=dict)
    note: Optional[str] = None
    existing_id: Optional[str] = None


@app.put("/api/credentials/n8n/{name}", tags=["credentials"])
def upsert_n8n_credential(
    name: str, body: N8NUpsertReq, req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    try:
        saved = set_n8n_credential(name, body.type, body.data, existing_id=body.existing_id)
    except UnknownN8NType as e:
        raise HTTPException(status_code=400, detail=str(e))
    except N8NError as e:
        raise HTTPException(status_code=502, detail=str(e))
    after = saved.pop("_after_hash", None)
    audit.record(
        action="create" if not body.existing_id else "update",
        source="n8n",
        name=name,
        integration=saved.get("integration"),
        before_hash=None,
        after_hash=after,
        note=body.note,
        client_ip=_client_ip(req),
        actor=actor,
    )
    return saved


@app.delete("/api/credentials/n8n/{name}", tags=["credentials"])
def clear_n8n_credential(
    name: str, req: Request, note: Optional[str] = None,
    actor: str = Depends(_current_actor),
) -> dict:
    # Resolve name -> id via the list endpoint so the caller doesn't need to
    # know n8n's internal id.
    items = list_n8n_credentials()
    match = next((c for c in items if c.get("name") == name), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"n8n credential '{name}' not found")
    try:
        res = delete_n8n_credential(match["n8n_id"])
    except N8NError as e:
        raise HTTPException(status_code=502, detail=str(e))
    audit.record(
        action="delete",
        source="n8n",
        name=name,
        integration=match.get("integration"),
        note=note,
        client_ip=_client_ip(req),
        actor=actor,
    )
    return {"name": name, **res}


# ---------------------------------------------------------------------------
# Kong credentials (DB-less: edit kong.yml + hot-reload via POST /config)
# ---------------------------------------------------------------------------

@app.put("/api/credentials/kong/{consumer}", tags=["credentials"])
def upsert_kong_credential(
    consumer: str, body: UpsertReq, req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    try:
        saved = set_kong_key(consumer, body.value)
    except UnknownKongConsumer as e:
        raise HTTPException(status_code=404, detail=str(e))
    except KongWriteError as e:
        raise HTTPException(status_code=400, detail=str(e))
    before = saved.pop("_before_hash", None)
    after  = saved.pop("_after_hash", None)
    audit.record(
        action="create" if before is None else "update",
        source="kong",
        name=consumer,
        integration=saved.get("integration"),
        before_hash=before,
        after_hash=after,
        note=body.note,
        client_ip=_client_ip(req),
        actor=actor,
    )
    return saved


@app.delete("/api/credentials/kong/{consumer}", tags=["credentials"])
def clear_kong_credential(
    consumer: str, req: Request, note: Optional[str] = None,
    actor: str = Depends(_current_actor),
) -> dict:
    try:
        cleared = delete_kong_key(consumer)
    except UnknownKongConsumer as e:
        raise HTTPException(status_code=404, detail=str(e))
    except KongWriteError as e:
        raise HTTPException(status_code=400, detail=str(e))
    before = cleared.pop("_before_hash", None)
    cleared.pop("_after_hash", None)
    audit.record(
        action="delete",
        source="kong",
        name=consumer,
        integration=cleared.get("integration"),
        before_hash=before,
        after_hash=None,
        note=note,
        client_ip=_client_ip(req),
        actor=actor,
    )
    return cleared


# ---------------------------------------------------------------------------
# Integration test (per-integration connection probe)
# ---------------------------------------------------------------------------

@app.get("/api/integrations", tags=["integrations"])
def list_integrations(actor: str = Depends(_current_actor)) -> dict:
    import integrations as reg
    return {
        "integrations": [
            {
                "key":           i.key or i.name.lower().replace(" ", "-"),
                "name":          i.name,
                "label":         i.label or i.name,
                "description":   i.description,
                "notes":         i.notes,
                "env_vars":      [ev.name for ev in i.env_vars],
                "kong_consumer": i.kong_consumer,
                "has_probe":     i.name in healthchecks.PROBES,
            }
            for i in reg.INTEGRATIONS
            if not i.hidden
        ]
    }


@app.post("/api/integrations/{name}/test", tags=["integrations"])
def test_integration(
    name: str, req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    result = healthchecks.run(name)
    audit.record(
        action="test",
        source="integration",
        name=name,
        integration=name.capitalize(),
        note=f"{'ok' if result['ok'] else 'fail'}: {result.get('detail','')[:200]}",
        client_ip=_client_ip(req),
        actor=actor,
    )
    return {"name": name, **result}


# ---------------------------------------------------------------------------
# Connector platform  (catalog · activate · test-event · topology · approvals)
# ---------------------------------------------------------------------------

import connector_store
connector_store.init_schema()

_KONG_PROXY_URL     = os.getenv("KONG_PROXY_URL",     "http://kong:8000")
_KONG_TIMEOUT       = float(os.getenv("KONG_TEST_TIMEOUT", "10"))
_REDPANDA_ADMIN_URL = os.getenv("REDPANDA_ADMIN_URL", "http://redpanda-0:9644")
_REDPANDA_REPLICAS  = int(os.getenv("REDPANDA_DEFAULT_REPLICAS", "3"))


def _ensure_topics(topics: list, actor: str) -> list[dict]:
    """Create Redpanda topics that don't exist yet via the Admin API.

    Non-fatal: returns a list of {name, status, detail} so callers can report
    partial success without blocking connector activation.
    """
    import httpx
    results = []
    for t in topics:
        try:
            r = httpx.post(
                f"{_REDPANDA_ADMIN_URL}/v1/topics",
                json={
                    "name":               t["name"],
                    "partition_count":    3,
                    "replication_factor": _REDPANDA_REPLICAS,
                    "configs": [
                        {"name": "retention.ms",
                         "value": str(t.get("retention_ms", 604_800_000))},
                        {"name": "compression.type", "value": "zstd"},
                    ],
                },
                timeout=5,
            )
            if r.status_code in (200, 201):
                results.append({"name": t["name"], "status": "created"})
            elif r.status_code == 400 and "already exists" in r.text.lower():
                results.append({"name": t["name"], "status": "exists"})
            else:
                results.append({"name": t["name"], "status": "error",
                                 "detail": r.text[:200]})
        except Exception as e:
            results.append({"name": t["name"], "status": "error",
                             "detail": str(e)[:200]})
    return results


def _serialize_integration(i) -> dict:
    """Full connector-type representation for the catalog and wizard."""
    import integrations as reg
    return {
        "key":             i.key or i.name.lower().replace(" ", "-"),
        "name":            i.name,
        "label":           i.label or i.name,
        "description":     i.description,
        "notes":           i.notes,
        "env_vars":        [
            {"name": ev.name, "kind": ev.kind, "services": ev.services}
            for ev in i.env_vars
        ],
        "kong_consumer":   i.kong_consumer,
        "has_probe":       i.name in healthchecks.PROBES,
        "topics":          [
            {"name": t.name, "direction": t.direction,
             "description": t.description, "retention_ms": t.retention_ms}
            for t in i.topics
        ],
        "field_schema":    [
            {"name": f.name, "type": f.type,
             "description": f.description, "required": f.required}
            for f in i.field_schema
        ],
        "transformations": [
            {"source": m.source, "target": m.target,
             "transform": m.transform, "note": m.note}
            for m in i.transformations
        ],
        "n8n_workflow_ids": i.n8n_workflow_ids,
    }


@app.get("/api/connector-types", tags=["connectors"])
def list_connector_types(actor: str = Depends(_current_actor)) -> dict:
    """Catalog of all connector types with full structural detail."""
    import integrations as reg
    states = {s["key"]: s for s in connector_store.list_states()}
    result = []
    for i in reg.INTEGRATIONS:
        if i.hidden:
            continue
        key = i.key or i.name.lower().replace(" ", "-")
        serialized = _serialize_integration(i)
        serialized["status"] = states.get(key, {}).get("status", "draft")
        result.append(serialized)
    return {"connector_types": result, "env": connector_store.GATEWAY_ENV}


@app.get("/api/connector-types/{key}", tags=["connectors"])
def get_connector_type(key: str, actor: str = Depends(_current_actor)) -> dict:
    import integrations as reg
    intg = reg.BY_KEY.get(key)
    if intg is None or intg.hidden:
        raise HTTPException(status_code=404, detail=f"connector type '{key}' not found")
    state = connector_store.get_state(key) or {}
    serialized = _serialize_integration(intg)
    serialized["status"] = state.get("status", "draft")
    serialized["activated_at"] = state.get("activated_at")
    serialized["activated_by"] = state.get("activated_by")
    return serialized


class ActivateReq(BaseModel):
    notes: Optional[str] = None
    creds: Optional[dict] = None   # {ENV_VAR_NAME: value} from wizard auth step
    topics: Optional[dict] = None  # {original_name: override_name} from wizard topics step


@app.post("/api/connector-types/{key}/activate", tags=["connectors"])
def activate_connector(
    key: str,
    body: ActivateReq,
    req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    """Activate a connector.

    Accepts credentials and topic overrides from the wizard. Saves creds to
    .env / Kong, creates any missing Redpanda topics, then activates the
    connector state (immediate in dev, queued in prod).
    """
    import integrations as reg
    intg = reg.BY_KEY.get(key)
    if intg is None:
        raise HTTPException(status_code=404, detail=f"unknown connector '{key}'")

    cred_results   = []
    topic_results  = []

    # ── save credentials ────────────────────────────────────────────────
    if body.creds:
        for ev in intg.env_vars:
            value = body.creds.get(ev.name, "").strip()
            if not value:
                continue
            # Route Kong-only creds to Kong consumer, everything else to .env
            is_kong_only = ev.services == ["kong"] or (
                intg.kong_consumer and
                ("INBOUND_API_KEY" in ev.name or "WEBHOOK_SECRET" in ev.name)
                and "kong" in ev.services
            )
            try:
                if is_kong_only and intg.kong_consumer:
                    set_kong_key(intg.kong_consumer, value)
                    cred_results.append({"name": ev.name, "target": "kong", "status": "saved"})
                else:
                    set_env_credential(ev.name, value)
                    audit.mark_restart_pending(ev.services)
                    cred_results.append({"name": ev.name, "target": "env", "status": "saved"})
                audit.record(
                    action="create",
                    source="kong" if is_kong_only else "env",
                    name=ev.name,
                    integration=intg.name,
                    note="wizard activation",
                    client_ip=_client_ip(req),
                    actor=actor,
                )
            except Exception as e:
                cred_results.append({"name": ev.name, "status": "error", "detail": str(e)[:200]})

    # ── create topics ────────────────────────────────────────────────────
    if intg.topics:
        overrides = body.topics or {}
        topics_to_create = [
            {**{"name": overrides.get(t.name, t.name),
                "direction": t.direction,
                "retention_ms": t.retention_ms}}
            for t in intg.topics
        ]
        topic_results = _ensure_topics(topics_to_create, actor)

    # ── flip activation state ────────────────────────────────────────────
    result = connector_store.activate(key, actor)
    audit.record(
        action="activate",
        source="connector",
        name=key,
        integration=intg.name,
        note=f"env={connector_store.GATEWAY_ENV} status={result['status']} "
             f"creds={len(cred_results)} topics={len(topic_results)}",
        client_ip=_client_ip(req),
        actor=actor,
    )
    return {**result, "creds": cred_results, "topics": topic_results}


class TestEventReq(BaseModel):
    payload: Optional[dict] = None   # if None, backend generates a synthetic sample


@app.post("/api/connector-types/{key}/test-event", tags=["connectors"])
def test_event(
    key: str,
    body: TestEventReq,
    req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    """Fire a test event through Kong for this connector and report the result.

    Generates a synthetic payload from the connector's field_schema if none is
    provided. Posts to {KONG_PROXY_URL}/{key} with the connector's inbound API
    key from .env. Returns HTTP status, latency, and correlation_id.
    """
    import integrations as reg
    import time, json

    intg = reg.BY_KEY.get(key)
    if intg is None or intg.hidden:
        raise HTTPException(status_code=404, detail=f"unknown connector '{key}'")

    # Build synthetic payload from field_schema if caller didn't provide one
    payload = body.payload
    if payload is None:
        payload = {}
        for f in intg.field_schema:
            if f.type == "string":
                payload[f.name] = f"test_{f.name}"
            elif f.type == "number":
                payload[f.name] = 0
            elif f.type == "boolean":
                payload[f.name] = False
            elif f.type in ("object", "array"):
                payload[f.name] = {} if f.type == "object" else []
        payload["_test"] = True

    # Resolve the inbound API key from env
    api_key_env = next(
        (ev.name for ev in intg.env_vars if "INBOUND_API_KEY" in ev.name or
         (ev.kind == "secret" and "API_KEY" in ev.name and ev.services == ["kong"])),
        None,
    )
    api_key = os.getenv(api_key_env, "") if api_key_env else ""

    import httpx
    url = f"{_KONG_PROXY_URL}/{key}"
    correlation_id = f"test-{uuid.uuid4().hex[:8]}"
    headers = {
        "Content-Type": "application/json",
        "X-Correlation-Id": correlation_id,
    }
    if api_key:
        headers["X-API-Key"] = api_key

    t0 = time.monotonic()
    try:
        r = httpx.post(
            url, json=payload, headers=headers,
            timeout=_KONG_TIMEOUT,
        )
        latency_ms = round((time.monotonic() - t0) * 1000)
        ok = r.status_code < 400
        detail = f"HTTP {r.status_code}"
        if not ok:
            detail += f": {r.text[:200]}"
    except httpx.TimeoutException:
        latency_ms = round(_KONG_TEST_TIMEOUT * 1000)
        ok, detail = False, "timeout waiting for Kong"
    except Exception as e:
        latency_ms = round((time.monotonic() - t0) * 1000)
        ok, detail = False, str(e)[:200]

    audit.record(
        action="test_event",
        source="connector",
        name=key,
        integration=key,
        note=f"ok={ok} {detail} latency={latency_ms}ms correlation={correlation_id}",
        client_ip=_client_ip(req),
        actor=actor,
    )
    return {
        "ok":             ok,
        "latency_ms":     latency_ms,
        "detail":         detail,
        "correlation_id": correlation_id,
        "payload_sent":   payload,
        "url":            url,
    }


@app.get("/api/topology", tags=["connectors"])
def get_topology(actor: str = Depends(_current_actor)) -> dict:
    """Return the data-flow topology: sources → topics → sinks.

    Used by the frontend topology graph. Each node is either a connector
    (source or sink) or a Redpanda topic. Edges represent publish/subscribe
    relationships.
    """
    import integrations as reg
    states = {s["key"]: s["status"] for s in connector_store.list_states()}

    nodes: list[dict] = []
    edges: list[dict] = []
    topic_set: set[str] = set()

    for intg in reg.INTEGRATIONS:
        if intg.hidden or not intg.topics:
            continue
        key = intg.key or intg.name.lower()
        label = intg.label or intg.name
        status = states.get(key, "draft")

        has_pub = any(t.direction == "publish" for t in intg.topics)
        has_sub = any(t.direction == "subscribe" for t in intg.topics)
        direction = (
            "bidirectional" if has_pub and has_sub
            else "source" if has_pub
            else "sink"
        )
        nodes.append({
            "id":        key,
            "label":     label,
            "type":      "connector",
            "direction": direction,
            "status":    status,
        })

        for t in intg.topics:
            if t.name not in topic_set:
                topic_set.add(t.name)
                nodes.append({
                    "id":    t.name,
                    "label": t.name,
                    "type":  "topic",
                })
            if t.direction == "publish":
                edges.append({"from": key, "to": t.name, "label": "pub"})
            else:
                edges.append({"from": t.name, "to": key, "label": "sub"})

    return {"nodes": nodes, "edges": edges}


# ── Approval queue (prod) ────────────────────────────────────────────────────

@app.get("/api/approvals", tags=["connectors"])
def list_approvals(actor: str = Depends(_current_actor)) -> dict:
    rows = connector_store.list_pending_approvals()
    return {"items": rows, "count": len(rows), "env": connector_store.GATEWAY_ENV}


class ApprovalDecisionReq(BaseModel):
    reason: Optional[str] = None


@app.post("/api/approvals/{approval_id}/approve", tags=["connectors"])
def approve_connector(
    approval_id: str,
    body: ApprovalDecisionReq,
    req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    result = connector_store.approve(approval_id, actor)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail"))
    audit.record(
        action="approve",
        source="connector",
        name=approval_id,
        note=body.reason or "",
        client_ip=_client_ip(req),
        actor=actor,
    )
    return result


@app.post("/api/approvals/{approval_id}/reject", tags=["connectors"])
def reject_connector(
    approval_id: str,
    body: ApprovalDecisionReq,
    req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    result = connector_store.reject(approval_id, actor, body.reason or "")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail"))
    audit.record(
        action="reject",
        source="connector",
        name=approval_id,
        note=body.reason or "",
        client_ip=_client_ip(req),
        actor=actor,
    )
    return result


# ---------------------------------------------------------------------------
# Service restart
# ---------------------------------------------------------------------------

class RestartReq(BaseModel):
    services: list[str] = Field(..., description="compose service names to restart")


@app.get("/api/services/pending", tags=["services"])
def pending_restarts(actor: str = Depends(_current_actor)) -> dict:
    rows = audit.pending_restarts()
    return {"items": rows, "count": len(rows)}


@app.post("/api/services/restart", tags=["services"])
def restart_services(
    body: RestartReq, req: Request,
    actor: str = Depends(_current_actor),
) -> dict:
    if not body.services:
        raise HTTPException(status_code=400, detail="no services specified")
    try:
        results = svc_mod.restart(body.services)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"docker daemon unreachable: {e}")
    # Mark succeeded services as no-longer-pending; leave failures alone so
    # the UI keeps prompting.
    succeeded = sorted({r["service"] for r in results if r.get("status") == "restarted"})
    audit.clear_restart_pending(succeeded)
    for s in succeeded:
        audit.record(
            action="restart",
            source="docker",
            name=s,
            integration=None,
            client_ip=_client_ip(req),
            actor=actor,
        )
    return {"results": results, "cleared": succeeded}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@app.get("/api/audit", tags=["audit"])
def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    name:  Optional[str] = Query(default=None),
    actor: str = Depends(_current_actor),
) -> dict:
    rows = audit.recent(limit=limit, name=name)
    return {"items": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    # Mount the rest of static so future CSS/JS files work without changing
    # this file.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index(actor: str = Depends(_current_actor)):
        target = STATIC_DIR / "index.html"
        if not target.exists():
            raise HTTPException(status_code=500, detail="index.html missing from static dir")
        return FileResponse(target)

    @app.get("/README.md", include_in_schema=False)
    def readme(actor: str = Depends(_current_actor)):
        target = STATIC_DIR / "README.md"
        if target.exists():
            return FileResponse(target, media_type="text/markdown; charset=utf-8")
        raise HTTPException(status_code=404)

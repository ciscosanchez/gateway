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
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
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
def list_integration_probes(actor: str = Depends(_current_actor)) -> dict:
    return {"probes": sorted(healthchecks.PROBES.keys())}


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

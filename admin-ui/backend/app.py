"""Gateway Admin UI backend.

Phase A: read-only env source.
Phase B: frontend wires up to /api/credentials.
Phase C (this commit): writes to .env + append-only SQLite audit log.
Phases D/E: n8n + Kong sources with their own write paths.

Safety rails:
- Plaintext values are never echoed back in responses. The client sees only
  value_masked (last 4 chars for secrets). Audit rows store sha256 hashes
  of before/after values, not the values themselves.
- Writes are atomic (rename into place). A failed write leaves the old
  file intact.
- Writes to unknown variable names are rejected (see REGISTRY in
  sources/env.py). Use the code to add a new var; the UI won't silently
  pollute .env.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import audit
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
from sources.n8n_api  import is_reachable as n8n_reachable,  list_n8n_credentials
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
# Health + meta
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["meta"])
def health() -> dict:
    docker_ok = False
    try:
        svc_mod.client().ping()
        docker_ok = True
    except Exception:
        pass
    return {
        "status": "ok",
        "phase":  "E2",
        "sources": {
            "env":  {"enabled": True,              "writable": True},
            "n8n":  {"enabled": n8n_reachable(),  "writable": False},
            "kong": {"enabled": kong_reachable(), "writable": kong_reachable()},
        },
        "restart": {"enabled": docker_ok},
    }


@app.get("/api/version", tags=["meta"])
def version() -> dict:
    return {"version": app.version}


# ---------------------------------------------------------------------------
# Credentials (read-only)
# ---------------------------------------------------------------------------

@app.get("/api/credentials", tags=["credentials"])
def list_credentials(
    source: Optional[str] = Query(default=None, description="env | n8n | kong"),
    integration: Optional[str] = Query(default=None),
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
def get_credential(name: str) -> dict:
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
def upsert_env_credential(name: str, body: UpsertReq, req: Request) -> dict:
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
    )
    audit.mark_restart_pending(services_for(name))
    return saved


@app.delete("/api/credentials/env/{name}", tags=["credentials"])
def clear_env_credential(name: str, req: Request, note: Optional[str] = None) -> dict:
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
    )
    audit.mark_restart_pending(services_for(name))
    return cleared


# ---------------------------------------------------------------------------
# Kong credentials (DB-less: edit kong.yml + hot-reload via POST /config)
# ---------------------------------------------------------------------------

@app.put("/api/credentials/kong/{consumer}", tags=["credentials"])
def upsert_kong_credential(consumer: str, body: UpsertReq, req: Request) -> dict:
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
    )
    return saved


@app.delete("/api/credentials/kong/{consumer}", tags=["credentials"])
def clear_kong_credential(consumer: str, req: Request, note: Optional[str] = None) -> dict:
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
    )
    return cleared


# ---------------------------------------------------------------------------
# Service restart
# ---------------------------------------------------------------------------

class RestartReq(BaseModel):
    services: list[str] = Field(..., description="compose service names to restart")


@app.get("/api/services/pending", tags=["services"])
def pending_restarts() -> dict:
    rows = audit.pending_restarts()
    return {"items": rows, "count": len(rows)}


@app.post("/api/services/restart", tags=["services"])
def restart_services(body: RestartReq, req: Request) -> dict:
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
        )
    return {"results": results, "cleared": succeeded}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@app.get("/api/audit", tags=["audit"])
def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    name:  Optional[str] = Query(default=None),
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
    def index():
        target = STATIC_DIR / "index.html"
        if not target.exists():
            raise HTTPException(status_code=500, detail="index.html missing from static dir")
        return FileResponse(target)

    @app.get("/README.md", include_in_schema=False)
    def readme():
        target = STATIC_DIR / "README.md"
        if target.exists():
            return FileResponse(target, media_type="text/markdown; charset=utf-8")
        raise HTTPException(status_code=404)

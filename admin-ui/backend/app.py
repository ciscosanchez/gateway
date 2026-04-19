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
from sources.env import (
    EnvWriteError,
    UnknownCredential,
    count_by_status,
    delete_env_credential,
    list_env_credentials,
    set_env_credential,
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
    return {
        "status": "ok",
        "phase":  "C",
        "sources": {
            "env":  {"enabled": True,  "writable": True},
            "n8n":  {"enabled": False, "writable": False},
            "kong": {"enabled": False, "writable": False},
        },
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
    if source in ("n8n", "kong"):
        # Phases D/E will populate these.
        return JSONResponse(
            status_code=501,
            content={
                "error": f"source '{source}' not implemented in phase A",
                "items": [],
            },
        )
    if integration:
        items = [i for i in items if i["integration"].lower() == integration.lower()]
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
    return cleared


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

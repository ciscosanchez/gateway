"""Gateway Admin UI backend.

Phase A (this file): read-only.
 - Serves the static HTML at /
 - Lists env-var-backed credentials from the gateway's .env
 - No writes, no audit log, no n8n/Kong API integration yet.

Phase B will wire the frontend to /api/credentials.
Phase C introduces env writes, audit log, and rotate-with-restart.
Phases D/E add n8n and Kong sources.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sources.env import list_env_credentials, count_by_status

app = FastAPI(
    title="Gateway Admin",
    version="0.1.0-phase-a",
    description="Unified credential / integration management for the gateway stack.",
)

STATIC_DIR = Path(os.getenv("ADMIN_STATIC_DIR", "/app/static"))


# ---------------------------------------------------------------------------
# Health + meta
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "phase":  "A",
        "sources": {
            "env":  {"enabled": True,  "writable": False},
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

"""Per-integration "test connection" probes.

Each probe reads the current credential(s) from the .env file (not the frozen
process env), makes one lightweight HTTP call to the integration, and returns
(ok: bool, detail: str).

Adding a probe for a new integration:
  1. Write a _myintegration() function here following the same pattern.
  2. Register it by calling _register("IntegrationName", _myintegration) below.
  That's it — no other file changes needed.

Probes only check reachability + auth. A green probe doesn't mean every
workflow will pass; it means the credential is valid right now.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

import httpx
from dotenv import dotenv_values

import integrations as reg

_ENV_FILE = Path(os.getenv("ADMIN_ENV_FILE", "/app/env/.env"))


def _env_val(name: str) -> str:
    """Read a value from the live .env file, not the frozen process env."""
    if not _ENV_FILE.exists():
        return os.getenv(name, "")
    return (dotenv_values(str(_ENV_FILE)).get(name) or "").strip()


def _probe(fn: Callable[[], Tuple[bool, str]]) -> dict:
    started = time.time()
    try:
        ok, detail = fn()
    except httpx.HTTPError as e:
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "detail": f"transport error: {e}"}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - started) * 1000),
                "detail": f"probe crashed: {e}"}
    return {"ok": ok, "latency_ms": int((time.time() - started) * 1000),
            "detail": detail}


# ── probe functions ───────────────────────────────────────────────────────────

def _samsara() -> Tuple[bool, str]:
    token = _env_val("SAMSARA_API_TOKEN")
    if not token or token == "CHANGE_ME":
        return False, "SAMSARA_API_TOKEN not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(
            "https://api.samsara.com/fleet/vehicles",
            params={"limit": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code == 200:
        return True, "Samsara API reachable, token accepted"
    if r.status_code == 401:
        return False, "Samsara rejected the token (401)"
    return False, f"Samsara returned {r.status_code}"


def _unigroup() -> Tuple[bool, str]:
    env = (_env_val("UNIGROUP_ENV") or "staging").lower()
    suffix = "PROD" if env == "production" else "STAGING"
    token_url   = _env_val(f"UNIGROUP_TOKEN_URL_{suffix}") or os.getenv(f"UNIGROUP_TOKEN_URL_{suffix}", "")
    client_id   = _env_val("UNIGROUP_CLIENT_ID")
    client_secret = _env_val("UNIGROUP_CLIENT_SECRET")
    scope       = _env_val("UNIGROUP_OAUTH_SCOPE")
    if not (token_url and client_id and client_secret) or client_id == "CHANGE_ME":
        return False, "UNIGROUP_CLIENT_ID / UNIGROUP_CLIENT_SECRET not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.post(token_url, data={
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": client_secret,
            "scope":         scope,
        })
    if r.status_code == 200 and "access_token" in (r.json() or {}):
        return True, f"Keycloak returned an access_token ({env})"
    return False, f"Keycloak {r.status_code}: {r.text[:200]}"


def _netsuite() -> Tuple[bool, str]:
    # OAuth1 TBA signing is non-trivial; check account id presence as a proxy.
    acct = _env_val("NETSUITE_ACCOUNT_ID")
    if not acct or acct == "CHANGE_ME":
        return False, "NETSUITE_ACCOUNT_ID not set"
    return True, f"account id present ({acct}); full OAuth1 sign-probe not yet implemented"


def _tai() -> Tuple[bool, str]:
    base_url = _env_val("TAI_API_URL")
    api_key  = _env_val("TAI_API_KEY")
    if not base_url or base_url == "CHANGE_ME":
        return False, "TAI_API_URL not set"
    if not api_key or api_key == "CHANGE_ME":
        return False, "TAI_API_KEY not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(
            f"{base_url}/Accounting/v2/Bills",
            params={"pageSize": 1},
            headers={"Authorization": f"ApiKey {api_key}", "Accept": "application/json"},
        )
    if r.status_code == 200:
        return True, "Tai API reachable, key accepted"
    if r.status_code == 401:
        return False, "Tai rejected the API key (401)"
    # 404 on the probe path still means auth passed — Tai swagger may differ
    if r.status_code == 404:
        return True, "Tai API reachable (404 on probe path, key likely valid)"
    return False, f"Tai API returned {r.status_code}"


def _wms() -> Tuple[bool, str]:
    api_url = _env_val("WMS_API_URL")
    api_key = _env_val("WMS_API_KEY")
    if not api_url or api_url == "CHANGE_ME":
        return False, "WMS_API_URL not set"
    if not api_key or api_key == "CHANGE_ME":
        return False, "WMS_API_KEY not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(
            api_url.rstrip("/") + "/health",
            headers={"X-API-Key": api_key},
        )
    if r.status_code in (200, 401):
        return r.status_code == 200, (
            "WMS API reachable, key accepted" if r.status_code == 200
            else "WMS rejected the API key (401)"
        )
    # 404 is acceptable — WMS may not have a /health route but auth passed
    if r.status_code == 404:
        return True, "WMS API reachable (404 on /health, key likely valid)"
    return False, f"WMS returned {r.status_code}"


def _dispatch() -> Tuple[bool, str]:
    api_url = _env_val("DISPATCH_API_URL")
    api_key = _env_val("DISPATCH_API_KEY")
    if not api_url or api_url == "CHANGE_ME":
        return False, "DISPATCH_API_URL not set"
    if not api_key or api_key == "CHANGE_ME":
        return False, "DISPATCH_API_KEY not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(
            api_url.rstrip("/") + "/health",
            headers={"X-API-Key": api_key},
        )
    if r.status_code in (200, 404):
        return True, f"Dispatch API reachable ({r.status_code})"
    if r.status_code == 401:
        return False, "Dispatch rejected the API key (401)"
    return False, f"Dispatch returned {r.status_code}"


# ── registry wiring ───────────────────────────────────────────────────────────
# Map integration names to probe functions, then patch them back into the
# Integration objects in the central registry so probe_for_integration() works.

_PROBE_FUNCTIONS: dict[str, Callable[[], Tuple[bool, str]]] = {
    "Samsara":  _samsara,
    "Unigroup": _unigroup,
    "NetSuite": _netsuite,
    "Tai":      _tai,
    "WMS":      _wms,
    "Dispatch": _dispatch,
}

for _intg in reg.INTEGRATIONS:
    if _intg.name in _PROBE_FUNCTIONS:
        _intg.probe = _PROBE_FUNCTIONS[_intg.name]

# Flat probe map keyed by lowercase integration name (for the /health API)
PROBES: dict[str, Callable[[], Tuple[bool, str]]] = {
    name.lower(): fn for name, fn in _PROBE_FUNCTIONS.items()
}


# ── public API ────────────────────────────────────────────────────────────────

def run(name: str) -> dict:
    fn = PROBES.get(name.lower())
    if fn is None:
        return {"ok": False, "latency_ms": 0,
                "detail": f"no probe for '{name}'. Known: {sorted(PROBES)}"}
    return _probe(fn)


def probe_for_integration(integration: str) -> Optional[str]:
    """Return the probe key for an integration name, or None if no probe."""
    key = integration.lower()
    return key if key in PROBES else None

"""Per-integration "test connection" probes.

Each probe reads the current credential(s) from the environment the admin-
ui container sees (same .env that was written by this service), then makes
one HTTP request to the integration's well-known ping-ish endpoint. Probes
return (ok: bool, latency_ms: int, detail: str).

Probes:
    samsara   -> GET https://api.samsara.com/fleet/vehicles?limit=1  (Bearer)
    unigroup  -> POST token endpoint (Keycloak) with client_credentials
    netsuite  -> (not implemented - OAuth1 TBA signing is non-trivial; TODO)

Probes only check reachability + auth. They don't exercise every downstream
path, so a green probe doesn't mean every workflow will pass - it means the
credential is valid right now.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Tuple

import httpx


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


def _samsara() -> Tuple[bool, str]:
    token = os.getenv("SAMSARA_API_TOKEN", "")
    if not token or token == "CHANGE_ME":
        return False, "SAMSARA_API_TOKEN not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(
            "https://api.samsara.com/fleet/vehicles",
            params={"limit": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code == 200:
        return True, "samsara API reachable, token accepted"
    if r.status_code == 401:
        return False, "samsara rejected the token (401)"
    return False, f"samsara returned {r.status_code}"


def _unigroup() -> Tuple[bool, str]:
    env = (os.getenv("UNIGROUP_ENV", "staging") or "staging").lower()
    suffix = "PROD" if env == "production" else "STAGING"
    token_url = os.getenv(f"UNIGROUP_TOKEN_URL_{suffix}", "")
    client_id = os.getenv("UNIGROUP_CLIENT_ID", "")
    client_secret = os.getenv("UNIGROUP_CLIENT_SECRET", "")
    scope = os.getenv("UNIGROUP_OAUTH_SCOPE", "") or ""
    if not (token_url and client_id and client_secret) or client_id == "CHANGE_ME":
        return False, "UNIGROUP_CLIENT_ID / UNIGROUP_CLIENT_SECRET not set"
    with httpx.Client(timeout=10.0) as c:
        r = c.post(
            token_url,
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
                "scope":         scope,
            },
        )
    if r.status_code == 200 and "access_token" in (r.json() or {}):
        return True, f"Keycloak returned an access_token ({env})"
    return False, f"Keycloak {r.status_code}: {r.text[:200]}"


def _netsuite() -> Tuple[bool, str]:
    # OAuth1 TBA signing is stateful enough that it's worth deferring to the
    # n8n credential-test endpoint once we have it. Until then this probe
    # just verifies the account id env is present.
    acct = os.getenv("NETSUITE_ACCOUNT_ID", "")
    if not acct or acct == "CHANGE_ME":
        return False, "NETSUITE_ACCOUNT_ID not set"
    return True, f"account id present ({acct}); OAuth1 TBA sign-probe not yet implemented"


PROBES = {
    "samsara":   _samsara,
    "unigroup":  _unigroup,
    "netsuite":  _netsuite,
}


def run(name: str) -> dict:
    fn = PROBES.get(name.lower())
    if fn is None:
        return {"ok": False, "latency_ms": 0,
                "detail": f"no probe for '{name}'. Known: {sorted(PROBES)}"}
    return _probe(fn)

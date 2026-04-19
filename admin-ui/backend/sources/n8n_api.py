"""Read n8n credentials via its REST API.

n8n exposes two APIs:
- Internal:   /rest/* (basic auth)              -> used by its own frontend
- Public v1:  /api/v1/* (X-N8N-API-KEY header)  -> generated in the n8n UI

The public API is stable but requires an API key that a human has to mint
through the UI. The internal API works off the basic-auth creds we already
have in .env, so it's the natural MVP integration point. When n8n someday
removes /rest we'll pivot to the public API.

Phase D (this file): read-only — list credential metadata (name + type).
Values are never returned by n8n's API, which is exactly the safety posture
we want. Writes land in Phase E.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")
N8N_BASIC_AUTH_USER = os.getenv("N8N_BASIC_AUTH_USER", "")
N8N_BASIC_AUTH_PASSWORD = os.getenv("N8N_BASIC_AUTH_PASSWORD", "")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")  # optional, preferred if set

# Mapping from n8n credential type identifiers to the integration we track.
# Unknown types just land with integration=None.
TYPE_TO_INTEGRATION = {
    "oAuth1Api":                "NetSuite",
    "httpBasicAuth":            "Infra",
    "httpHeaderAuth":           "Infra",
    "kafka":                    "Infra",
    "redisKey":                 "Infra",
    "postgres":                 "Infra",
    "slackApi":                 "Alerting",
    "pagerDutyApi":             "Alerting",
}


def _client() -> httpx.Client:
    if N8N_API_KEY:
        headers = {"X-N8N-API-KEY": N8N_API_KEY}
        return httpx.Client(base_url=N8N_BASE_URL, headers=headers, timeout=5.0)
    auth = None
    if N8N_BASIC_AUTH_USER and N8N_BASIC_AUTH_PASSWORD:
        auth = (N8N_BASIC_AUTH_USER, N8N_BASIC_AUTH_PASSWORD)
    return httpx.Client(base_url=N8N_BASE_URL, auth=auth, timeout=5.0)


def is_reachable() -> bool:
    try:
        with _client() as c:
            r = c.get("/healthz")
            return r.status_code < 500
    except Exception:
        return False


def list_n8n_credentials() -> list[dict]:
    """List credential records in n8n. Returns an empty list on failure so the
    UI degrades gracefully — connectivity state is surfaced via /api/health.
    """
    endpoints = []
    if N8N_API_KEY:
        endpoints.append("/api/v1/credentials")
    # Fall through to the internal API as well; it's the one we expect to work
    # in the default basic-auth setup.
    endpoints.append("/rest/credentials")

    last_err: Optional[str] = None
    for path in endpoints:
        try:
            with _client() as c:
                r = c.get(path)
            if r.status_code >= 400:
                last_err = f"{path} -> {r.status_code}"
                continue
            data = r.json()
            # Both APIs return {"data": [...]} in recent n8n versions. Older
            # versions returned a bare list; handle both.
            rows = data.get("data", data) if isinstance(data, dict) else data
            items: list[dict] = []
            for row in rows or []:
                credential_type = row.get("type") or row.get("nodesAccess", [{}])[0].get("nodeType", "")
                items.append({
                    "name":         row.get("name") or row.get("id"),
                    "integration":  TYPE_TO_INTEGRATION.get(credential_type) or "—",
                    "source":       "n8n",
                    "kind":         "n8n-credential",
                    "value_masked": f"{credential_type} credential",
                    "status":       "ok",
                    "is_placeholder": False,
                    "rotated_at":   row.get("updatedAt"),
                    "n8n_id":       row.get("id"),
                    "n8n_type":     credential_type,
                })
            return items
        except Exception as e:
            last_err = str(e)
            continue
    # Log once through stdout; callers already degrade to empty list.
    if last_err:
        print(f"[admin-ui] n8n credential list failed: {last_err}", flush=True)
    return []

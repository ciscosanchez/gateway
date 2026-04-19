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


# Whitelist of credential types we accept via the admin UI. Anything outside
# this list requires using n8n's own Credentials screen - we don't want to
# be a half-implemented schema-forwarder for every one of n8n's 600+ types.
WRITABLE_TYPES = {
    # Generic - covers API-key-in-header integrations (Samsara API, WMS, etc.)
    "httpHeaderAuth": {
        "fields": ["name", "value"],
        "secrets": ["value"],
        "label":  "HTTP Header Auth",
        "hint":   "For API-key-in-header integrations (Samsara, WMS, generic REST)",
    },
    "httpBasicAuth": {
        "fields": ["user", "password"],
        "secrets": ["password"],
        "label":  "HTTP Basic Auth",
        "hint":   "Username + password",
    },
    # NetSuite TBA (OAuth1)
    "oAuth1Api": {
        "fields":  ["consumerKey", "consumerSecret", "accessToken", "accessTokenSecret", "signatureMethod", "realm"],
        "secrets": ["consumerSecret", "accessTokenSecret"],
        "label":   "OAuth1 (NetSuite TBA)",
        "hint":    "signatureMethod=HMAC-SHA256; realm=your NetSuite account id",
    },
}


class N8NError(RuntimeError):
    pass


class UnknownN8NType(N8NError):
    pass


def _require_known_type(type_name: str) -> dict:
    meta = WRITABLE_TYPES.get(type_name)
    if meta is None:
        raise UnknownN8NType(
            f"n8n credential type '{type_name}' is not supported from the admin UI. "
            f"Known: {sorted(WRITABLE_TYPES)}. For other types use n8n's Credentials screen."
        )
    return meta


def _validate_data(type_name: str, data: dict) -> dict:
    meta = _require_known_type(type_name)
    missing = [f for f in meta["fields"] if f not in data or data[f] == ""]
    if missing:
        raise N8NError(f"missing fields for {type_name}: {missing}")
    return {k: data[k] for k in meta["fields"] if k in data}


def _hash_secrets(type_name: str, data: dict) -> str:
    """Stable hash over the secret fields only, for audit before/after."""
    import hashlib
    meta = WRITABLE_TYPES.get(type_name, {})
    parts = [f"{k}={data.get(k, '')}" for k in meta.get("secrets", [])]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _n8n_create_path() -> str:
    return "/api/v1/credentials" if N8N_API_KEY else "/rest/credentials"


def _n8n_item_path(cred_id: str) -> str:
    base = "/api/v1" if N8N_API_KEY else "/rest"
    return f"{base}/credentials/{cred_id}"


def set_n8n_credential(name: str, type_name: str, data: dict, existing_id: Optional[str] = None) -> dict:
    meta = _require_known_type(type_name)
    clean = _validate_data(type_name, data)
    payload = {"name": name, "type": type_name, "data": clean}
    # Internal /rest/credentials expects nodesAccess for legacy reasons; the
    # public /api/v1 accepts the simpler shape.
    if not N8N_API_KEY:
        payload["nodesAccess"] = []
    try:
        with _client() as c:
            if existing_id:
                r = c.patch(_n8n_item_path(existing_id), json=payload)
            else:
                r = c.post(_n8n_create_path(), json=payload)
    except httpx.HTTPError as e:
        raise N8NError(f"n8n API unreachable: {e}") from e
    if r.status_code >= 400:
        raise N8NError(f"n8n credential save failed: {r.status_code} {r.text[:500]}")
    body = r.json() if r.text else {}
    # n8n wraps its responses; unwrap if present
    row = body.get("data", body)
    return {
        "name":           row.get("name") or name,
        "integration":    TYPE_TO_INTEGRATION.get(type_name) or "—",
        "source":         "n8n",
        "kind":           "n8n-credential",
        "value_masked":   f"{type_name} credential",
        "status":         "ok",
        "is_placeholder": False,
        "rotated_at":     row.get("updatedAt"),
        "n8n_id":         row.get("id") or existing_id,
        "n8n_type":       type_name,
        "_after_hash":    _hash_secrets(type_name, clean),
    }


def delete_n8n_credential(cred_id: str) -> dict:
    try:
        with _client() as c:
            r = c.delete(_n8n_item_path(cred_id))
    except httpx.HTTPError as e:
        raise N8NError(f"n8n API unreachable: {e}") from e
    if r.status_code >= 400 and r.status_code != 404:
        raise N8NError(f"n8n credential delete failed: {r.status_code} {r.text[:500]}")
    return {"n8n_id": cred_id, "deleted": r.status_code < 400}


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

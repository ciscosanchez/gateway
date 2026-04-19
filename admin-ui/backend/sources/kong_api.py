"""Read + (phase E2) write Kong consumers + their key-auth credentials.

Kong runs DB-less in this stack, which has two implications:

Reads work normally through the admin API:
    GET /consumers
    GET /consumers/{id}/key-auth

Writes do NOT work through the individual-resource admin endpoints — those
return 405 in DB-less mode. Phase E2 will instead edit kong/kong.yml
atomically and POST /config to reload Kong in place.

Phase E1 (this file): read-only.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

KONG_ADMIN_URL = os.getenv("KONG_ADMIN_URL", "http://kong:8001")

# Map well-known consumer usernames to the integration we display. Unknown
# consumers show up with integration "—" which is fine.
USERNAME_TO_INTEGRATION = {
    "samsara-client":   "Samsara",
    "netsuite-client":  "NetSuite",
    "unigroup-client":  "Unigroup",
    "wms-client":       "WMS",
    "dispatch-client":  "Dispatch",
}


def _client() -> httpx.Client:
    return httpx.Client(base_url=KONG_ADMIN_URL, timeout=5.0)


def is_reachable() -> bool:
    try:
        with _client() as c:
            r = c.get("/status")
            return r.status_code < 500
    except Exception:
        return False


def _mask_key(key: str) -> str:
    if not key:
        return "(unset)"
    if len(key) <= 8:
        return "•••"
    return f"•••{key[-4:]}"


def _iso_from_unix(ts) -> Optional[str]:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def list_kong_credentials() -> list[dict]:
    """One row per key-auth credential. If a consumer has no keys yet, still
    emit a row so the UI shows the consumer exists but is unconfigured.
    """
    items: list[dict] = []
    try:
        with _client() as c:
            r = c.get("/consumers")
            if r.status_code >= 400:
                print(f"[admin-ui] kong /consumers -> {r.status_code}", flush=True)
                return []
            consumers = r.json().get("data", [])
            for cons in consumers:
                cid = cons.get("id")
                username = cons.get("username") or cid
                integration = USERNAME_TO_INTEGRATION.get(username, "—")
                # Per-consumer key-auth listing; this endpoint works in DB-less too.
                kr = c.get(f"/consumers/{cid}/key-auth")
                keys = kr.json().get("data", []) if kr.status_code < 400 else []
                if not keys:
                    items.append({
                        "name":           username,
                        "integration":    integration,
                        "source":         "kong",
                        "kind":           "kong-consumer",
                        "value_masked":   "(no key configured)",
                        "status":         "missing",
                        "is_placeholder": True,
                        "rotated_at":     _iso_from_unix(cons.get("created_at")),
                        "kong_consumer_id": cid,
                    })
                else:
                    for key in keys:
                        items.append({
                            "name":           username,
                            "integration":    integration,
                            "source":         "kong",
                            "kind":           "kong-consumer",
                            "value_masked":   f"X-API-Key {_mask_key(key.get('key', ''))}",
                            "status":         "ok",
                            "is_placeholder": False,
                            "rotated_at":     _iso_from_unix(key.get("created_at")),
                            "kong_consumer_id": cid,
                            "kong_key_id":      key.get("id"),
                        })
    except Exception as e:
        print(f"[admin-ui] kong credential list failed: {e}", flush=True)
    return items

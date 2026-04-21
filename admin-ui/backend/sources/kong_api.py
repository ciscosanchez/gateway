"""Read + write Kong consumers + their key-auth credentials.

Kong runs DB-less in this stack:
    - Reads:  normal admin-API endpoints work (GET /consumers, /key-auth, etc.)
    - Writes: individual-resource endpoints return 405 in DB-less mode.
              We instead edit kong/kong.yml (comments preserved via ruamel)
              and POST the full rendered config to Kong /config. On success
              we atomically swap the file on disk so the next container
              restart loads the same state.

Ordering of a write:
    1. Load kong.yml (preserving comments / quoting / key order)
    2. Mutate the in-memory structure
    3. Dump to a YAML string
    4. POST /config with that string - if Kong rejects it (400), abort
    5. Only then os.replace() the file on disk
    This way the runtime state and the on-disk file never diverge.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from ruamel.yaml import YAML

KONG_ADMIN_URL = os.getenv("KONG_ADMIN_URL", "http://kong:8001")
KONG_DECLARATIVE_FILE = Path(os.getenv("KONG_DECLARATIVE_FILE", "/app/kong/kong.yml"))

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 10000   # never line-wrap lua snippets in plugins[*].config.access

# Map well-known consumer usernames to the integration we display. Unknown
# consumers show up with integration "—" which is fine.
# Derived from integrations.py — add new consumers there, not here.
from integrations import KONG_CONSUMER_MAP
USERNAME_TO_INTEGRATION: dict[str, str] = KONG_CONSUMER_MAP


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


# ---------------------------------------------------------------------------
# Writes (DB-less: edit kong.yml atomically, hot-reload via POST /config)
# ---------------------------------------------------------------------------

class KongWriteError(RuntimeError):
    pass


class UnknownKongConsumer(KongWriteError):
    pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_kong_yml():
    if not KONG_DECLARATIVE_FILE.exists():
        raise KongWriteError(f"kong.yml not found at {KONG_DECLARATIVE_FILE}")
    with open(KONG_DECLARATIVE_FILE, "r", encoding="utf-8") as f:
        return _yaml.load(f)


def _dump_to_string(data) -> str:
    buf = io.StringIO()
    _yaml.dump(data, buf)
    return buf.getvalue()


def _post_config(yaml_str: str) -> None:
    """Hot-reload Kong with a new declarative config. Kong validates before
    applying; an invalid config returns 400 and the running config is
    unchanged. We only proceed to write the file after this succeeds.

    All transport failures are surfaced as KongWriteError so the API layer
    catches a single exception type.
    """
    try:
        with httpx.Client(base_url=KONG_ADMIN_URL, timeout=30.0) as c:
            r = c.post("/config", json={"config": yaml_str})
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
        raise KongWriteError(f"Kong admin API unreachable at {KONG_ADMIN_URL}: {e}") from e
    if r.status_code >= 400:
        raise KongWriteError(f"Kong /config rejected: {r.status_code} {r.text[:800]}")


def _atomic_write(content: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".kong.yml.tmp.", dir=str(KONG_DECLARATIVE_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        # Preserve original permissions if the file existed
        try:
            st = KONG_DECLARATIVE_FILE.stat()
            os.chmod(tmp, st.st_mode)
        except FileNotFoundError:
            pass
        os.replace(tmp, KONG_DECLARATIVE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _find_consumer(data, username: str):
    for cons in data.get("consumers", []) or []:
        if cons.get("username") == username:
            return cons
    return None


def set_kong_key(consumer_username: str, new_key: str) -> dict:
    if not new_key or len(new_key) < 16:
        raise KongWriteError("API key must be at least 16 characters")
    data = _load_kong_yml()
    target = _find_consumer(data, consumer_username)
    if target is None:
        raise UnknownKongConsumer(f"consumer '{consumer_username}' not in kong.yml")

    old_keys = [k.get("key", "") for k in (target.get("keyauth_credentials") or [])]
    old_key = old_keys[0] if old_keys else ""
    target["keyauth_credentials"] = [{"key": new_key}]

    new_yaml = _dump_to_string(data)
    _post_config(new_yaml)   # if Kong rejects, disk is untouched
    _atomic_write(new_yaml)

    return {
        "name":           consumer_username,
        "integration":    USERNAME_TO_INTEGRATION.get(consumer_username, "—"),
        "source":         "kong",
        "kind":           "kong-consumer",
        "value_masked":   f"X-API-Key {_mask_key(new_key)}",
        "status":         "ok",
        "is_placeholder": False,
        "rotated_at":     None,
        "_before_hash":   _sha256(old_key) if old_key else None,
        "_after_hash":    _sha256(new_key),
    }


def delete_kong_key(consumer_username: str) -> dict:
    """Remove key-auth credential from a consumer. The consumer record stays
    but will 401 until a new key is set.
    """
    data = _load_kong_yml()
    target = _find_consumer(data, consumer_username)
    if target is None:
        raise UnknownKongConsumer(f"consumer '{consumer_username}' not in kong.yml")

    old_keys = [k.get("key", "") for k in (target.get("keyauth_credentials") or [])]
    old_key = old_keys[0] if old_keys else ""
    target["keyauth_credentials"] = []

    new_yaml = _dump_to_string(data)
    _post_config(new_yaml)
    _atomic_write(new_yaml)

    return {
        "name":           consumer_username,
        "integration":    USERNAME_TO_INTEGRATION.get(consumer_username, "—"),
        "source":         "kong",
        "kind":           "kong-consumer",
        "value_masked":   "(no key configured)",
        "status":         "missing",
        "is_placeholder": True,
        "rotated_at":     None,
        "_before_hash":   _sha256(old_key) if old_key else None,
        "_after_hash":    None,
    }

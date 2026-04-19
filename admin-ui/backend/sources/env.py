"""Read + write credentials from the gateway's .env file.

The list of known vars and their classification lives in REGISTRY below.
Adding a var there is how you surface it in the admin UI.

Values are masked according to kind:
  - secret     -> show the last 4 chars only
  - identifier -> show as-is (NetSuite account id, etc.)
  - config     -> show as-is (URLs, scopes, flags)

Writes are atomic: we parse the file into lines, rewrite the target line
(or append if new), write to a temp file in the same directory, and use
os.replace() to swap. Comments and formatting are preserved.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

from dotenv import dotenv_values

ENV_FILE = Path(os.getenv("ADMIN_ENV_FILE", "/app/env/.env"))

# services is the compose-service list that consumes the variable; writing
# it marks those services as needing a restart. Keep this conservative: it's
# cheaper to restart one extra service than to miss one.
N8N = ["n8n", "n8n-worker"]

REGISTRY: dict[str, dict] = {
    # --- Samsara ---
    "SAMSARA_API_TOKEN":          {"integration": "Samsara",  "kind": "secret", "services": N8N},
    "SAMSARA_WEBHOOK_SECRET":     {"integration": "Samsara",  "kind": "secret", "services": N8N + ["kong"]},

    # --- NetSuite ---
    "NETSUITE_ACCOUNT_ID":        {"integration": "NetSuite", "kind": "identifier", "services": N8N},
    "NETSUITE_CONSUMER_KEY":      {"integration": "NetSuite", "kind": "secret",     "services": N8N},
    "NETSUITE_CONSUMER_SECRET":   {"integration": "NetSuite", "kind": "secret",     "services": N8N},
    "NETSUITE_TOKEN_ID":          {"integration": "NetSuite", "kind": "secret",     "services": N8N},
    "NETSUITE_TOKEN_SECRET":      {"integration": "NetSuite", "kind": "secret",     "services": N8N},

    # --- Unigroup Converge ---
    "UNIGROUP_ENV":               {"integration": "Unigroup", "kind": "config", "services": N8N},
    "UNIGROUP_CLIENT_ID":         {"integration": "Unigroup", "kind": "secret", "services": N8N},
    "UNIGROUP_CLIENT_SECRET":     {"integration": "Unigroup", "kind": "secret", "services": N8N},
    "UNIGROUP_OAUTH_SCOPE":       {"integration": "Unigroup", "kind": "config", "services": N8N},

    # --- WMS ---
    "WMS_API_KEY":                {"integration": "WMS",      "kind": "secret", "services": N8N},
    "WMS_API_URL":                {"integration": "WMS",      "kind": "config", "services": N8N},

    # --- Dispatch ---
    "DISPATCH_API_KEY":           {"integration": "Dispatch", "kind": "secret", "services": N8N},
    "DISPATCH_API_URL":           {"integration": "Dispatch", "kind": "config", "services": N8N},

    # --- Infrastructure ---
    "POSTGRES_PASSWORD":          {"integration": "Infra", "kind": "secret", "services": ["postgres"] + N8N + ["postgres-exporter"]},
    "REDIS_PASSWORD":             {"integration": "Infra", "kind": "secret", "services": ["redis", "kong"] + N8N + ["redis-exporter"]},
    "N8N_BASIC_AUTH_PASSWORD":    {"integration": "Infra", "kind": "secret", "services": N8N},
    "N8N_ENCRYPTION_KEY":         {"integration": "Infra", "kind": "secret", "services": N8N},
    "GF_SECURITY_ADMIN_PASSWORD": {"integration": "Infra", "kind": "secret", "services": ["grafana"]},

    # --- Alerting / backup ---
    "ALERTMANAGER_SLACK_WEBHOOK": {"integration": "Alerting", "kind": "secret", "services": ["alertmanager"]},
    "ALERTMANAGER_PAGERDUTY_KEY": {"integration": "Alerting", "kind": "secret", "services": ["alertmanager"]},
    "ZAMMAD_URL":                 {"integration": "Zammad",   "kind": "config", "services": N8N},
    "ZAMMAD_API_TOKEN":           {"integration": "Zammad",   "kind": "secret", "services": N8N},
    "ZAMMAD_GROUP":               {"integration": "Zammad",   "kind": "config", "services": N8N},
    "ZAMMAD_CUSTOMER":            {"integration": "Zammad",   "kind": "config", "services": N8N},
    "BACKUP_AGE_RECIPIENT":       {"integration": "Backup",   "kind": "config", "services": []},

    # --- Admin UI gate ---
    "ADMIN_UI_USER":              {"integration": "Infra", "kind": "identifier", "services": []},
    "ADMIN_UI_PASSWORD":          {"integration": "Infra", "kind": "secret",     "services": []},
}


def services_for(name: str) -> list[str]:
    meta = REGISTRY.get(name)
    return list(meta.get("services", [])) if meta else []

PLACEHOLDER_PREFIXES = ("CHANGE_ME", "REPLACE_ME", "REPLACE-ME")


def _is_placeholder(value: str) -> bool:
    if not value:
        return True
    return any(value.startswith(p) for p in PLACEHOLDER_PREFIXES)


def _mask(value: str, kind: str) -> str:
    if kind == "secret":
        return f"•••{value[-4:]}" if len(value) > 6 else "•••"
    return value  # identifier / config — safe to show in clear


def list_env_credentials() -> list[dict]:
    """Return one entry per known env var, whether set or not.

    Unknown vars in .env are ignored on purpose: we want the UI to enumerate
    what the gateway *expects*, not leak every random variable that got put
    in there.
    """
    env = dotenv_values(str(ENV_FILE)) if ENV_FILE.exists() else {}
    items: list[dict] = []
    for name, meta in REGISTRY.items():
        raw = (env.get(name) or "").strip()
        placeholder = _is_placeholder(raw)
        items.append({
            "name":         name,
            "integration":  meta["integration"],
            "source":       "env",
            "kind":         meta["kind"],
            "value_masked": _mask(raw, meta["kind"]) if not placeholder else "(unset)",
            "status":       "missing" if placeholder else "ok",
            "is_placeholder": placeholder,
            # Last rotated is not known from .env alone; Phase C will read
            # this from the audit log once writes go through the backend.
            "rotated_at":   None,
        })
    return items


def count_by_status(items: Iterable[dict]) -> dict[str, int]:
    c = {"ok": 0, "missing": 0}
    for it in items:
        c[it["status"]] = c.get(it["status"], 0) + 1
    return c


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

class EnvWriteError(RuntimeError):
    pass


class UnknownCredential(EnvWriteError):
    pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _read_lines() -> list[str]:
    if not ENV_FILE.exists():
        return []
    return ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)


def _escape_value(value: str) -> str:
    # Only quote when the value would be ambiguous (has spaces, # or
    # starts with a quote). Keeps diffs small on plain values.
    needs_quote = any(c in value for c in " \t#'\"\\") or value.startswith(("'", '"'))
    if not needs_quote:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def set_env_credential(name: str, value: str) -> dict:
    """Write or update a known env var. Returns the fresh masked item.

    Raises UnknownCredential if the name isn't in REGISTRY — we refuse to
    let the admin UI silently leak new names into .env.
    """
    meta = REGISTRY.get(name)
    if meta is None:
        raise UnknownCredential(f"{name} is not a known gateway variable")

    if not ENV_FILE.exists():
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE.touch()

    old_raw = (dotenv_values(str(ENV_FILE)).get(name) or "").strip()

    lines = _read_lines()
    new_line = f"{name}={_escape_value(value)}\n"
    replaced = False
    out: list[str] = []
    for line in lines:
        m = _LINE_RE.match(line)
        if m and m.group(1) == name:
            out.append(new_line)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(new_line)

    # Atomic rewrite in the same directory so os.replace is rename(2).
    fd, tmp_path = tempfile.mkstemp(prefix=".env.tmp.", dir=str(ENV_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(out)
        # Preserve original permissions if the file existed
        try:
            st = ENV_FILE.stat()
            os.chmod(tmp_path, st.st_mode)
        except FileNotFoundError:
            pass
        os.replace(tmp_path, ENV_FILE)
    except PermissionError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise EnvWriteError(
            f"cannot write {ENV_FILE}: {e}. The admin container runs as uid 1000; "
            f"on Linux hosts you may need `chmod 644 .env && chgrp 1000 .env` so "
            f"the bind-mount is writable."
        ) from e
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Rebuild masked representation
    placeholder = _is_placeholder(value)
    return {
        "name":         name,
        "integration":  meta["integration"],
        "source":       "env",
        "kind":         meta["kind"],
        "value_masked": _mask(value, meta["kind"]) if not placeholder else "(unset)",
        "status":       "missing" if placeholder else "ok",
        "is_placeholder": placeholder,
        "rotated_at":   None,
        # Hashes for the audit log; never surface these in the client response
        "_before_hash": _sha256(old_raw) if old_raw else None,
        "_after_hash":  _sha256(value),
    }


def delete_env_credential(name: str) -> dict:
    """Blank a known env var (set to empty) rather than removing the line.

    Removing the line entirely would make the gateway fall back to whatever
    default the compose file provides with ${VAR:-default}, which is often
    the wrong behavior for secrets. Empty-string is explicit.
    """
    meta = REGISTRY.get(name)
    if meta is None:
        raise UnknownCredential(f"{name} is not a known gateway variable")

    old_raw = (dotenv_values(str(ENV_FILE)).get(name) or "").strip() if ENV_FILE.exists() else ""
    updated = set_env_credential(name, "")
    # After-hash is the hash of empty string, which is public info; replace
    # it with None so the audit row makes the intent clear.
    updated["_before_hash"] = _sha256(old_raw) if old_raw else None
    updated["_after_hash"]  = None
    return updated

"""Read credentials from the gateway's .env file.

The list of known vars and their classification lives in REGISTRY below.
Adding a var there is how you surface it in the admin UI.

Values are masked according to kind:
  - secret     -> show the last 4 chars only
  - identifier -> show as-is (NetSuite account id, etc.)
  - config     -> show as-is (URLs, scopes, flags)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from dotenv import dotenv_values

ENV_FILE = Path(os.getenv("ADMIN_ENV_FILE", "/app/env/.env"))

REGISTRY: dict[str, dict] = {
    # --- Samsara ---
    "SAMSARA_API_TOKEN":          {"integration": "Samsara",  "kind": "secret"},
    "SAMSARA_WEBHOOK_SECRET":     {"integration": "Samsara",  "kind": "secret"},

    # --- NetSuite ---
    "NETSUITE_ACCOUNT_ID":        {"integration": "NetSuite", "kind": "identifier"},
    "NETSUITE_CONSUMER_KEY":      {"integration": "NetSuite", "kind": "secret"},
    "NETSUITE_CONSUMER_SECRET":   {"integration": "NetSuite", "kind": "secret"},
    "NETSUITE_TOKEN_ID":          {"integration": "NetSuite", "kind": "secret"},
    "NETSUITE_TOKEN_SECRET":      {"integration": "NetSuite", "kind": "secret"},

    # --- Unigroup Converge ---
    "UNIGROUP_ENV":               {"integration": "Unigroup", "kind": "config"},
    "UNIGROUP_CLIENT_ID":         {"integration": "Unigroup", "kind": "secret"},
    "UNIGROUP_CLIENT_SECRET":     {"integration": "Unigroup", "kind": "secret"},
    "UNIGROUP_OAUTH_SCOPE":       {"integration": "Unigroup", "kind": "config"},

    # --- WMS ---
    "WMS_API_KEY":                {"integration": "WMS",      "kind": "secret"},
    "WMS_API_URL":                {"integration": "WMS",      "kind": "config"},

    # --- Infrastructure (still secrets, surfaced for rotation) ---
    "POSTGRES_PASSWORD":          {"integration": "Infra",    "kind": "secret"},
    "REDIS_PASSWORD":             {"integration": "Infra",    "kind": "secret"},
    "N8N_BASIC_AUTH_PASSWORD":    {"integration": "Infra",    "kind": "secret"},
    "N8N_ENCRYPTION_KEY":         {"integration": "Infra",    "kind": "secret"},
    "GF_SECURITY_ADMIN_PASSWORD": {"integration": "Infra",    "kind": "secret"},

    # --- Alerting / backup ---
    "ALERTMANAGER_SLACK_WEBHOOK": {"integration": "Alerting", "kind": "secret"},
    "ALERTMANAGER_PAGERDUTY_KEY": {"integration": "Alerting", "kind": "secret"},
    "BACKUP_AGE_RECIPIENT":       {"integration": "Backup",   "kind": "config"},
}

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

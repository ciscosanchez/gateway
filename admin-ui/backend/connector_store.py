"""Connector activation state and approval queue.

Tracks whether each integration is active, pending approval, or disabled.
Approval queue is only relevant when GATEWAY_ENV=prod — in dev, connectors
activate immediately on wizard submit.

States:
  active           -- running; credentials and workflows are deployed
  pending_approval -- wizard submitted in prod; waiting for a human to approve
  approved         -- approved but not yet activated (brief transition state)
  disabled         -- manually disabled; config retained
  draft            -- wizard in progress; not yet submitted

Existing connectors are seeded as 'active' at startup if no record exists.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import integrations as reg

AUDIT_DB = Path(os.getenv("ADMIN_AUDIT_DB", "/app/data/audit.db"))
AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)

GATEWAY_ENV: str = os.getenv("GATEWAY_ENV", "dev").lower()  # "dev" | "prod"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    with _lock:
        c = sqlite3.connect(str(AUDIT_DB), isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()


def init_schema() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS connector_states (
              key          TEXT PRIMARY KEY,
              status       TEXT NOT NULL DEFAULT 'active',
              env          TEXT NOT NULL DEFAULT 'dev',
              created_at   TEXT NOT NULL,
              activated_at TEXT,
              activated_by TEXT,
              notes        TEXT
            );

            CREATE TABLE IF NOT EXISTS connector_approvals (
              id              TEXT PRIMARY KEY,
              connector_key   TEXT NOT NULL,
              requested_by    TEXT NOT NULL,
              requested_at    TEXT NOT NULL,
              reviewed_by     TEXT,
              reviewed_at     TEXT,
              decision        TEXT,    -- 'approved' | 'rejected'
              reason          TEXT,
              FOREIGN KEY (connector_key) REFERENCES connector_states(key)
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_key
              ON connector_approvals(connector_key);
            CREATE INDEX IF NOT EXISTS idx_approvals_pending
              ON connector_approvals(decision) WHERE decision IS NULL;
        """)
    _seed_existing()


def _seed_existing() -> None:
    """Seed all non-hidden integrations as 'active' if they have no record yet."""
    now = _now()
    with _conn() as c:
        for intg in reg.INTEGRATIONS:
            if intg.hidden:
                continue
            key = intg.key or intg.name.lower()
            c.execute(
                """
                INSERT OR IGNORE INTO connector_states
                  (key, status, env, created_at, activated_at, activated_by, notes)
                VALUES (?, 'active', ?, ?, ?, 'system', 'Pre-existing connector seeded at startup')
                """,
                (key, GATEWAY_ENV, now, now),
            )


# ── reads ────────────────────────────────────────────────────────────────────

def get_state(key: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM connector_states WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None


def list_states() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM connector_states ORDER BY key"
        ).fetchall()
        return [dict(r) for r in rows]


def list_pending_approvals() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """
            SELECT a.*, cs.key as connector_key
            FROM connector_approvals a
            JOIN connector_states cs ON a.connector_key = cs.key
            WHERE a.decision IS NULL
            ORDER BY a.requested_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ── writes ───────────────────────────────────────────────────────────────────

def activate(key: str, actor: str) -> dict:
    """Activate immediately (dev) or queue for approval (prod).

    Returns {"status": "active"|"pending_approval", "approval_id": str|None}.
    """
    now = _now()
    if GATEWAY_ENV == "prod":
        approval_id = str(uuid.uuid4())
        with _conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO connector_states
                  (key, status, env, created_at, activated_at, activated_by)
                VALUES (?, 'pending_approval', ?, ?, NULL, NULL)
                """,
                (key, GATEWAY_ENV, now),
            )
            c.execute(
                """
                INSERT INTO connector_approvals
                  (id, connector_key, requested_by, requested_at)
                VALUES (?, ?, ?, ?)
                """,
                (approval_id, key, actor, now),
            )
        return {"status": "pending_approval", "approval_id": approval_id}
    else:
        with _conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO connector_states
                  (key, status, env, created_at, activated_at, activated_by)
                VALUES (?, 'active', ?, ?, ?, ?)
                """,
                (key, GATEWAY_ENV, now, now, actor),
            )
        return {"status": "active", "approval_id": None}


def approve(approval_id: str, actor: str) -> dict:
    """Approve a pending connector and mark it active."""
    now = _now()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM connector_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "detail": "approval not found"}
        if row["decision"] is not None:
            return {"ok": False, "detail": f"already {row['decision']}"}
        c.execute(
            "UPDATE connector_approvals SET reviewed_by=?, reviewed_at=?, decision='approved' WHERE id=?",
            (actor, now, approval_id),
        )
        c.execute(
            "UPDATE connector_states SET status='active', activated_at=?, activated_by=? WHERE key=?",
            (now, actor, row["connector_key"]),
        )
    return {"ok": True, "status": "active"}


def reject(approval_id: str, actor: str, reason: str = "") -> dict:
    """Reject a pending connector approval."""
    now = _now()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM connector_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "detail": "approval not found"}
        if row["decision"] is not None:
            return {"ok": False, "detail": f"already {row['decision']}"}
        c.execute(
            "UPDATE connector_approvals SET reviewed_by=?, reviewed_at=?, decision='rejected', reason=? WHERE id=?",
            (actor, now, reason, approval_id),
        )
        c.execute(
            "UPDATE connector_states SET status='disabled' WHERE key=?",
            (row["connector_key"],),
        )
    return {"ok": True, "status": "disabled"}


def disable(key: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE connector_states SET status='disabled' WHERE key=?", (key,)
        )

"""Append-only audit log backed by SQLite.

Every write through the admin backend goes through record(). The database
file lives in a named volume so it survives container restarts but is
isolated from the bind-mounts we use for config.

Intentionally minimal: we don't expose update or delete for audit rows.
If you need to export/rotate the log, dump the SQLite file and start a new
one - don't try to edit it in place.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

AUDIT_DB = Path(os.getenv("ADMIN_AUDIT_DB", "/app/data/audit.db"))
AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    # sqlite3 connections aren't thread-safe by default; serialize all writes
    # through a single lock. Reads could be parallel but volume here is tiny.
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
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              ts           TEXT    NOT NULL,
              actor        TEXT    NOT NULL,
              action       TEXT    NOT NULL,     -- create | update | delete | rotate | restart
              source       TEXT    NOT NULL,     -- env | n8n | kong | docker
              name         TEXT    NOT NULL,     -- credential id OR service name
              integration  TEXT,                 -- Samsara, NetSuite, ...
              before_hash  TEXT,                 -- sha256 of old value (never plaintext)
              after_hash   TEXT,                 -- sha256 of new value
              note         TEXT,
              client_ip    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_name ON audit_events(name);

            -- Tracks which compose services are waiting on a restart because
            -- an env var they consume was changed. Cleared when that service
            -- is restarted through the admin UI.
            CREATE TABLE IF NOT EXISTS pending_restarts (
              service          TEXT PRIMARY KEY,
              first_change_ts  TEXT NOT NULL,
              last_change_ts   TEXT NOT NULL,
              change_count     INTEGER NOT NULL DEFAULT 1
            );
            """
        )


def record(
    *,
    action: str,
    source: str,
    name: str,
    integration: str | None = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    actor: str = "admin",
    note: str | None = None,
    client_ip: str | None = None,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO audit_events
              (ts, actor, action, source, name, integration,
               before_hash, after_hash, note, client_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_now_iso(), actor, action, source, name, integration,
             before_hash, after_hash, note, client_ip),
        )
        return int(cur.lastrowid or 0)


def mark_restart_pending(services: list[str]) -> None:
    """Record that the named compose services have un-applied env changes."""
    if not services:
        return
    now = _now_iso()
    with _conn() as c:
        for svc in services:
            c.execute(
                """
                INSERT INTO pending_restarts (service, first_change_ts, last_change_ts, change_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(service) DO UPDATE SET
                  last_change_ts = excluded.last_change_ts,
                  change_count   = change_count + 1
                """,
                (svc, now, now),
            )


def clear_restart_pending(services: list[str]) -> None:
    if not services:
        return
    with _conn() as c:
        c.executemany(
            "DELETE FROM pending_restarts WHERE service = ?",
            [(s,) for s in services],
        )


def pending_restarts() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT service, first_change_ts, last_change_ts, change_count FROM pending_restarts ORDER BY service"
        ).fetchall()
        return [dict(r) for r in rows]


def recent(limit: int = 50, name: str | None = None) -> list[dict]:
    q = "SELECT * FROM audit_events"
    args: tuple = ()
    if name:
        q += " WHERE name = ?"
        args = (name,)
    q += " ORDER BY ts DESC LIMIT ?"
    args = args + (limit,)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
        return [dict(r) for r in rows]

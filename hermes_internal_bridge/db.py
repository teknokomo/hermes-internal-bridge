"""SQLite storage for the shared bus (spec §9.3): one database per host.

The database lives on the root Hermes home so every profile of the host sees
the same queue. Writes are short transactions guarded by a process-local lock
(one writer per process; WAL keeps readers cheap). Idempotency by ``relay_id``
is enforced by the schema (unique index), not by application code (FR-011).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .paths import ensure_private_dir

# --- migrations -------------------------------------------------------------

MIGRATION_0001_BASE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hib_routes (
  route_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  source_json TEXT NOT NULL,
  target_json TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('handoff','mirror')),
  priority INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hib_messages (
  relay_id TEXT PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  source_profile TEXT NOT NULL,
  target_profile TEXT NOT NULL,
  envelope_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','delivered','processing','done','failed','dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_until REAL,
  next_attempt_at REAL,
  last_error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS hib_messages_relay_idx ON hib_messages(relay_id);
CREATE INDEX IF NOT EXISTS hib_messages_status_idx ON hib_messages(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS hib_handoffs (
  relay_id TEXT PRIMARY KEY REFERENCES hib_messages(relay_id),
  summary TEXT NOT NULL,
  constraints_json TEXT NOT NULL DEFAULT '[]',
  expected_result TEXT NOT NULL DEFAULT '',
  deadline_at REAL,
  priority TEXT NOT NULL DEFAULT 'normal',
  accepted_by TEXT NOT NULL DEFAULT '',
  completed_by TEXT NOT NULL DEFAULT '',
  completed_at REAL
);

CREATE TABLE IF NOT EXISTS hib_artifacts (
  artifact_id TEXT PRIMARY KEY,
  relay_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  uri TEXT NOT NULL,
  owner_profile TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hib_ledger (
  id TEXT PRIMARY KEY,
  relay_id TEXT NOT NULL,
  event TEXT NOT NULL,
  route_id TEXT NOT NULL DEFAULT '',
  details_json TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""

# Operational columns beyond the §9.3 baseline: intake bookkeeping for the
# relay (kind, ack-once, payload text for reply correlation, route audit).
MIGRATION_0002_RELAY_OPS = """
ALTER TABLE hib_messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat.message';
ALTER TABLE hib_messages ADD COLUMN payload_text TEXT NOT NULL DEFAULT '';
ALTER TABLE hib_messages ADD COLUMN payload_size INTEGER NOT NULL DEFAULT 0;
ALTER TABLE hib_messages ADD COLUMN route_id TEXT NOT NULL DEFAULT '';
ALTER TABLE hib_messages ADD COLUMN source_notified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE hib_messages ADD COLUMN relay_mirror INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS hib_messages_target_idx ON hib_messages(target_profile, status, next_attempt_at);
CREATE INDEX IF NOT EXISTS hib_messages_kind_idx ON hib_messages(kind, status);
CREATE INDEX IF NOT EXISTS hib_messages_mirror_idx ON hib_messages(relay_mirror, status);
"""

# Directory heartbeats (spec FR-025): one row per profile instance.
MIGRATION_0003_DIRECTORY = """
CREATE TABLE IF NOT EXISTS hib_heartbeats (
  profile TEXT PRIMARY KEY,
  updated_at REAL NOT NULL
);
"""

MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0001_base", MIGRATION_0001_BASE),
    ("0002_relay_ops", MIGRATION_0002_RELAY_OPS),
    ("0003_directory", MIGRATION_0003_DIRECTORY),
)


class Database:
    """A single SQLite connection with short guarded transactions."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        ensure_private_dir(self.path.parent)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=30.0, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # ------------------------------------------------------------------ setup

    def migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            applied = {
                row["version"]
                for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                # executescript runs in autocommit; the marker row below is the
                # applied-guard and is written immediately after the script.
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, time.time()),
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- primitives

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A short ``BEGIN IMMEDIATE`` transaction; commits on success, rolls back on error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # ------------------------------------------------------------------ helpers

    def applied_migrations(self) -> list[str]:
        rows = self.query("SELECT version FROM schema_migrations ORDER BY version")
        return [row["version"] for row in rows]


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else {key: row[key] for key in row.keys()}

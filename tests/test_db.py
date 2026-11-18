"""Shared database (spec §9.3): migrations, uniqueness, privacy, durability."""

import os
import stat
import sqlite3
import sys

import pytest

from hermes_internal_bridge.db import Database


def test_migrations_applied_once(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    db.migrate()
    assert db.applied_migrations() == ["0001_base", "0002_relay_ops", "0003_directory"]
    db.close()


def test_relay_id_uniqueness_enforced_by_schema(tmp_path):
    """FR-011: dedupe is a database guarantee, not an application check."""
    db = Database(tmp_path / "hib.sqlite3")
    now = 1.0
    db.execute(
        "INSERT INTO hib_messages (relay_id, correlation_id, source_profile, target_profile,"
        " envelope_json, status, attempts, created_at, updated_at) VALUES"
        " ('r-1','c-1','a','b','{}','queued',0,?,?)",
        (now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO hib_messages (relay_id, correlation_id, source_profile, target_profile,"
            " envelope_json, status, attempts, created_at, updated_at) VALUES"
            " ('r-1','c-2','a','b','{}','queued',0,?,?)",
            (now, now),
        )
    db.close()


def test_status_check_constraint(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO hib_messages (relay_id, correlation_id, source_profile, target_profile,"
            " envelope_json, status, attempts, created_at, updated_at) VALUES"
            " ('r-1','c-1','a','b','{}','wat',0,1,1)"
        )
    db.close()


def test_wal_mode(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    mode = db.query_one("PRAGMA journal_mode")["journal_mode"]
    assert mode.lower() == "wal"
    db.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_private_dir_posix_0700(tmp_path):
    from hermes_internal_bridge.paths import ensure_private_dir

    path = ensure_private_dir(tmp_path / "private")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o700


def test_transaction_rollback_on_error(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    try:
        with db.transaction() as conn:
            conn.execute("INSERT INTO hib_heartbeats (profile, updated_at) VALUES ('x', 1)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.query_one("SELECT * FROM hib_heartbeats WHERE profile='x'") is None
    db.close()

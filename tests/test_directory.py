"""Profile directory (spec §7.4): roster from config, heartbeats, statuses."""

import time

from hermes_internal_bridge.db import Database
from hermes_internal_bridge.directory import Directory

ROSTER = [
    {"profile": "target-profile", "description": "Исполнитель", "capabilities": ["code", "research"]},
    {"profile": "other", "description": "", "capabilities": []},
]


def make(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    return db, Directory(db, roster=ROSTER, refresh_seconds=60)


def test_roster_from_config_only(tmp_path):
    """FR-021/FR-023: roster via official config; memory and credentials are not read."""
    db, directory = make(tmp_path)
    entries = directory.entries()
    assert [e.profile for e in entries] == ["other", "target-profile"]
    assert entries[1].capabilities == ["code", "research"]
    assert entries[1].status == "offline"
    db.close()


def test_unknown_profile_is_not_known(tmp_path):
    db, directory = make(tmp_path)
    assert directory.known("target-profile") is True
    assert directory.known("ghost") is False
    assert directory.status_for("ghost") is None
    db.close()


def test_heartbeat_makes_profile_online(tmp_path):
    db, directory = make(tmp_path)
    directory.heartbeat("target-profile")
    assert directory.status_for("target-profile") == "online"
    db.close()


def test_stale_heartbeat_is_offline(tmp_path):
    db, directory = make(tmp_path)
    db.execute("INSERT INTO hib_heartbeats (profile, updated_at) VALUES ('target-profile', ?)",
               (time.time() - 1000,))
    assert directory.stale("target-profile") is True
    assert directory.status_for("target-profile") == "offline"
    db.close()


def test_busy_when_processing_with_live_lease(tmp_path):
    """FR-022: busy is derivable from bus processing activity."""
    db, directory = make(tmp_path)
    from hermes_internal_bridge.bus import MessageBus
    from helpers import enqueue_message

    directory.heartbeat("target-profile")
    bus = MessageBus(db)
    enqueue_message(bus)
    bus.claim("target-profile")  # processing with a live lease
    counts = {"target-profile": {"processing": 1}}
    assert directory.status_for("target-profile", bus_counts=counts) == "busy"
    db.close()

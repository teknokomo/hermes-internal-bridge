"""Ledger (FR-042, FR-043): audit trail with the secrets filter applied before the write."""

from hermes_internal_bridge.db import Database
from hermes_internal_bridge.ledger import Ledger


def test_event_records_relay_and_route(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    ledger = Ledger(db)
    ledger.event("r-1", "relay_enqueued", route_id="route-1", details={"target_profile": "b"})
    records = ledger.recent()
    assert len(records) == 1
    record = records[0]
    assert record["relay_id"] == "r-1"
    assert record["route_id"] == "route-1"
    assert record["details"]["target_profile"] == "b"
    db.close()


def test_secrets_never_reach_the_ledger(tmp_path):
    """FR-043: filter applies before the write — zero secret matches afterwards."""
    db = Database(tmp_path / "hib.sqlite3")
    ledger = Ledger(db)
    ledger.event("r-1", "relay_failed", details={
        "error": "api_key=sk-abc123DEF456ghi789JKL отвергнут",
        "headers": {"Authorization": "Bearer abcdefghijklmno123456789"},
        "note": "чистый текст",
    })
    import json

    raw = json.dumps(ledger.all_records(), ensure_ascii=False)
    assert "sk-" not in raw
    assert "Bearer abcdefghijklmno123456789" not in raw
    assert "api_key=" not in raw
    assert "чистый текст" in raw
    db.close()


def test_count_events(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    ledger = Ledger(db)
    ledger.event("r-1", "relay_filtered_forwarded")
    ledger.event("r-2", "relay_filtered_forwarded")
    ledger.event("r-3", "relay_enqueued")
    assert ledger.count_events("relay_filtered_forwarded") == 2
    assert ledger.count_events("relay_enqueued") == 1
    db.close()


def test_recent_for_relay(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    ledger = Ledger(db)
    ledger.event("r-1", "relay_enqueued")
    ledger.event("r-1", "relay_delivered")
    ledger.event("r-2", "relay_enqueued")
    records = ledger.recent(relay_id="r-1")
    assert {r["event"] for r in records} == {"relay_enqueued", "relay_delivered"}
    db.close()

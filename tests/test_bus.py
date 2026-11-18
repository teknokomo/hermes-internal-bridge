"""Local bus (spec §7.5): states, lease, retry, dead-letter, idempotency, scoping."""

import time

import pytest

from helpers import enqueue_message

from hermes_internal_bridge.db import Database
from hermes_internal_bridge.errors import BridgeError
from hermes_internal_bridge.bus import MessageBus


@pytest.fixture()
def bus(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    yield MessageBus(db, lease_seconds=60, max_attempts=3, backoff_seconds=[5, 30, 120])
    db.close()


def test_enqueue_and_claim(bus):
    envelope = enqueue_message(bus)
    row = bus.claim("target-profile")
    assert row is not None
    assert row["relay_id"] == envelope.relay_id
    assert row["status"] == "processing"
    assert row["lease_until"] is not None


def test_duplicate_relay_id_is_confirmation(bus):
    """FR-011: the second message is not created; callers get relay_duplicate."""
    envelope = enqueue_message(bus, relay_id="same-id")
    with pytest.raises(BridgeError) as excinfo:
        enqueue_message(bus, relay_id="same-id", text="другой текст")
    assert excinfo.value.code == "relay_duplicate"
    rows = bus.list_by_status("queued", target_profile="target-profile")
    assert len(rows) == 1


def test_claim_scoped_by_target_profile(bus):
    """The consumer for profile A cannot see or claim messages addressed to profile B."""
    enqueue_message(bus, destination={"instance_id": "local", "profile": "profile-b", "chat_id": "c"})
    assert bus.claim("profile-a") is None
    assert bus.claim("profile-b") is not None
    row = bus.get_any("no-such")
    assert row is None


def test_profile_scope_on_get(bus):
    envelope = enqueue_message(bus)
    assert bus.get(envelope.relay_id, target_profile="target-profile") is not None
    assert bus.get(envelope.relay_id, target_profile="profile-a") is None


def test_lease_expiry_returns_to_queue_and_counts_attempt(bus):
    envelope = enqueue_message(bus)
    row = bus.claim("target-profile")
    # simulate a crashed consumer: lease passes
    bus.release_expired_leases(now=time.time() + 120)
    row = bus.get(row["relay_id"], target_profile="target-profile")
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["last_error"] == "bus_lease_expired"
    assert row["next_attempt_at"] > time.time()  # backoff before the next try


def test_backoff_blocks_claim_until_due(bus):
    envelope = enqueue_message(bus)
    row = bus.claim("target-profile")
    bus.fail(row["relay_id"], target_profile="target-profile", error="boom")
    after_fail = bus.get(row["relay_id"], target_profile="target-profile")
    assert after_fail["status"] == "queued"
    assert bus.claim("target-profile") is None  # backoff not elapsed yet
    row = bus.claim("target-profile", now=time.time() + 60)
    assert row is not None


def test_dead_letter_after_exhausted_attempts(bus):
    """FR-031: dead-letter only for the operator, no infinite retry."""
    envelope = enqueue_message(bus)
    dead = False
    for _ in range(10):
        row = bus.claim("target-profile", now=time.time() + 3600)  # skip backoff
        assert row is not None
        try:
            bus.fail(row["relay_id"], target_profile="target-profile", error="processing error")
        except BridgeError as exc:
            assert exc.code == "bus_dead_letter"
            dead = True
            break
    assert dead
    final = bus.get(envelope.relay_id, target_profile="target-profile")
    assert final["status"] == "dead"
    assert final["attempts"] == 3  # max_attempts


def test_availability_failure_never_counts_attempts(bus):
    """FR-006: target unavailable → no loss, message stays queued, no dead-letter."""
    envelope = enqueue_message(bus)
    row = bus.claim("target-profile")
    updated = bus.fail(row["relay_id"], target_profile="target-profile",
                       error="relay_target_unavailable", counts_attempt=False)
    assert updated["status"] == "queued"
    assert updated["attempts"] == 0
    assert updated["source_notified"] == 0


def test_source_notified_once(bus):
    envelope = enqueue_message(bus)
    row = bus.claim("target-profile")
    bus.fail(row["relay_id"], target_profile="target-profile", error="x", counts_attempt=False)
    bus.mark_source_notified(row["relay_id"], target_profile="target-profile")
    row = bus.get(row["relay_id"], target_profile="target-profile")
    assert row["source_notified"] == 1


def test_mark_delivered_and_complete(bus):
    envelope = enqueue_message(bus)
    row = bus.claim("target-profile")
    assert bus.mark_delivered(row["relay_id"], target_profile="target-profile") is True
    assert bus.complete(row["relay_id"], target_profile="target-profile", completed_by="target-profile")
    final = bus.get(row["relay_id"], target_profile="target-profile")
    assert final["status"] == "done"


def test_find_for_reply_correlation(bus):
    envelope = enqueue_message(bus, text="уникальный текст задачи")
    row = bus.claim("target-profile")
    bus.set_payload_text(row["relay_id"], target_profile="target-profile", text="[hib:r=x;h=1] уникальный текст задачи")
    found = bus.find_for_reply(target_profile="target-profile", payload_text="[hib:r=x;h=1] уникальный текст задачи")
    assert found["relay_id"] == envelope.relay_id
    assert bus.correlation_id_for(envelope.relay_id) == envelope.correlation_id
    assert bus.find_for_reply(target_profile="profile-a", payload_text="x") is None


def test_claim_display_picks_mirror_but_not_agent(bus):
    agent = enqueue_message(bus)
    enqueue_message(bus, relay_id="m-1", relay_mirror=True, destination={
        "instance_id": "local", "profile": "source-profile", "chat_id": "dm-chat-1", "thread_id": "",
    })
    # display claim takes the mirror addressed to source-profile
    claimed = bus.claim_display("source-profile")
    assert claimed["relay_id"] == "m-1"
    # agent-bound messages are not display messages
    assert bus.claim_display("target-profile") is None
    claimed_agent = bus.claim("target-profile")
    assert claimed_agent["relay_id"] == agent.relay_id


def test_display_claim_scoped_to_profile(bus):
    enqueue_message(bus, relay_mirror=True, destination={
        "instance_id": "local", "profile": "profile-b", "chat_id": "c",
    })
    assert bus.claim_display("profile-a") is None
    assert bus.claim_display("profile-b") is not None


def test_counts(bus):
    enqueue_message(bus, relay_id="a")
    enqueue_message(bus, relay_id="b")
    counts = bus.counts()
    assert counts["queued"] == 2


def test_purge_done_keeps_other_states(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    bus = MessageBus(db)
    done_env = enqueue_message(bus, relay_id="done-1")
    bus.set_status("done-1", "done", target_profile="target-profile")
    enqueue_message(bus, relay_id="queued-1", text="другое")
    removed = bus.purge_done(older_than=time.time() + 1)
    assert removed == 1
    assert bus.get_any("done-1") is None
    assert bus.get_any("queued-1") is not None
    db.close()
